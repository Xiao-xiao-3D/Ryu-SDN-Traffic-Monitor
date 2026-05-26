# -*- coding: utf-8 -*-
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, tcp, udp, icmp, arp
from ryu.lib import hub
from collections import defaultdict
import time
import json

# ================= REST API 相关导入 =================
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response

# ================= 告警配置 =================
ALERT_CONFIG = {
    'high_traffic': {
        'threshold_bps': 10000000,  # 10Mbps 流量阈值
        'duration_seconds': 10,     # 持续时间(秒)
        'severity': 'WARNING',      # 严重级别
        'message': '检测到交换机 {switch} 端口 {port} 流量过高: {speed:.2f} Mbps'  # 告警消息模板
    },
    'port_utilization': {
        'threshold_percent': 80,    # 80% 端口利用率阈值
        'severity': 'CRITICAL',     # 严重级别
        'message': '交换机 {switch} 端口 {port} 利用率过高: {utilization:.1f}%'  # 告警消息模板
    },
    'traffic_burst': {
        'threshold_multiplier': 3.0,  # 3倍正常流量
        'duration_seconds': 5,        # 持续时间(秒)
        'severity': 'WARNING',        # 严重级别
        'message': '检测到交换机 {switch} 端口 {port} 流量突发: {current_speed:.2f} Mbps (正常: {normal_speed:.2f} Mbps)'  # 告警消息模板
    },
    'port_errors': {
        'error_threshold': 100,  # 100个错误
        'severity': 'ERROR',     # 严重级别
        'message': '交换机 {switch} 端口 {port} 错误率过高: {errors} 个错误'  # 告警消息模板
    },
    'business_anomaly': {
        'threshold_multiplier': 2.0,  # 2倍正常业务流量
        'severity': 'INFO',           # 严重级别
        'message': '检测到异常 {} 流量: {:.2f} Mbps (正常: {:.2f} Mbps)'  # 告警消息模板
    }
}

# ================= 业务规则定义 =================
BUSINESS_RULES = {
    'ICMP': {
        'name': 'ICMP',
        'description': '网络诊断流量(ICMP)',
        'rules': [
            {'ip_proto': 1}  # IP协议号1表示ICMP
        ],
        'priority': 100,     # 优先级
        'color': '#FF6B6B',  # 颜色标识
        'normal_traffic_bps': 1000000,  # 正常流量阈值 1Mbps
        'qos': {
            'min_rate': 100000,    # 最小速率
            'max_rate': 1000000,   # 最大速率
            'queue_id': 1          # 队列ID
        }
    },
    'HTTP': {
        'name': 'HTTP',
        'description': 'Web服务流量(HTTP/HTTPS)',  # Web服务(HTTP/HTTPS)
        'rules': [
            {'tcp_dst': 80},   # HTTP端口
            {'tcp_dst': 443},  # HTTPS端口
        ],
        'priority': 95,        # 优先级
        'color': '#4ECDC4',    # 颜色标识
        'normal_traffic_bps': 5000000,  # 正常流量阈值 5Mbps
        'qos': {
            'min_rate': 1000000,
            'max_rate': 10000000,
            'queue_id': 2
        }
    },
    'TCP': {
        'name': 'TCP',
        'description': 'TCP通用流量',
        'rules': [
            {'ip_proto': 6}  # IP协议号6表示TCP
        ],
        'priority': 80,      # 优先级
        'color': '#FFD166',  # 颜色标识
        'normal_traffic_bps': 2000000,  # 正常流量阈值 2Mbps
        'qos': {
            'min_rate': 500000,
            'max_rate': 5000000,
            'queue_id': 3
        }
    },
    'UDP': {
        'name': 'UDP',
        'description': 'UDP通用流量',
        'rules': [
            {'ip_proto': 17}  # IP协议号17表示UDP
        ],
        'priority': 70,      # 优先级
        'color': '#06D6A0',  # 颜色标识
        'normal_traffic_bps': 2000000,  # 正常流量阈值 2Mbps
        'qos': {
            'min_rate': 500000,
            'max_rate': 5000000,
            'queue_id': 4
        }
    }
}


# ================= REST API 控制器类 =================
class TrafficMonitorAPIController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(TrafficMonitorAPIController, self).__init__(req, link, data, **config)
        self.monitor_app = data['monitor_app']

    @route('traffic', '/api/v1/stats/ports', methods=['GET'])
    def get_port_stats(self, req, **kwargs):
        """获取所有端口统计信息"""
        port_stats = []
        for (dpid, port_no), (rx_bytes, tx_bytes) in self.monitor_app.prev_port_bytes.items():
            rx_speed = tx_speed = 0.0
            key = (dpid, port_no)
            if key in self.monitor_app.port_stats:
                stats = self.monitor_app.port_stats[key]
                rx_speed = stats.get('rx_speed', 0)
                tx_speed = stats.get('tx_speed', 0)

            port_stats.append({
                'switch_id': "{:016x}".format(dpid),  # Python 2.7兼容
                'port': port_no,
                'rx_bytes': rx_bytes,
                'tx_bytes': tx_bytes,
                'rx_speed_bps': rx_speed,
                'tx_speed_bps': tx_speed,
                'total_bytes': rx_bytes + tx_bytes
            })

        return Response(
            content_type='application/json',
            body=json.dumps({
                'success': True,
                'data': port_stats,
                'timestamp': time.time(),
                'count': len(port_stats)
            }, indent=2)
        )

    @route('traffic', '/api/v1/stats/ports/{dpid}/{port_no}', methods=['GET'])
    def get_specific_port_stats(self, req, **kwargs):
        """获取指定交换机和端口的统计信息"""
        try:
            dpid_str = kwargs['dpid']
            port_no_str = kwargs['port_no']

            # 尝试十六进制或十进制转换
            try:
                dpid = int(dpid_str, 16)  # 十六进制字符串转整数
            except ValueError:
                dpid = int(dpid_str)  # 尝试十进制

            port_no = int(port_no_str)

            key = (dpid, port_no)
            if key not in self.monitor_app.prev_port_bytes:
                return Response(
                    status=404,
                    content_type='application/json',
                    body=json.dumps({
                        'success': False,
                        'error': 'Port {} on switch {} not found'.format(port_no_str, dpid_str)
                    })
                )

            rx_bytes, tx_bytes = self.monitor_app.prev_port_bytes[key]
            rx_speed = tx_speed = 0.0
            if key in self.monitor_app.port_stats:
                stats = self.monitor_app.port_stats[key]
                rx_speed = stats.get('rx_speed', 0)
                tx_speed = stats.get('tx_speed', 0)

            # 计算利用率（假设1G端口）
            util_rx = (rx_speed / (1000.0 * 1000.0 * 1000.0)) * 100 if rx_speed > 0 else 0
            util_tx = (tx_speed / (1000.0 * 1000.0 * 1000.0)) * 100 if tx_speed > 0 else 0

            return Response(
                content_type='application/json',
                body=json.dumps({
                    'success': True,
                    'data': {
                        'switch_id': dpid_str,
                        'port': port_no,
                        'rx_bytes': rx_bytes,
                        'tx_bytes': tx_bytes,
                        'rx_speed_bps': rx_speed,
                        'tx_speed_bps': tx_speed,
                        'total_bytes': rx_bytes + tx_bytes,
                        'utilization_rx': "{:.2f}%".format(util_rx),
                        'utilization_tx': "{:.2f}%".format(util_tx)
                    },
                    'timestamp': time.time()
                }, indent=2)
            )
        except ValueError as e:
            return Response(
                status=400,
                content_type='application/json',
                body=json.dumps({
                    'success': False,
                    'error': 'Invalid switch ID or port number format: {}'.format(str(e))
                })
            )
        except Exception as e:
            return Response(
                status=500,
                content_type='application/json',
                body=json.dumps({
                    'success': False,
                    'error': 'Internal server error: {}'.format(str(e))
                })
            )

    @route('traffic', '/api/v1/stats/business', methods=['GET'])
    def get_business_stats(self, req, **kwargs):
        """获取业务流量统计"""
        business_stats = {}
        for biz_type, stats in self.monitor_app.business_stats.items():
            business_stats[biz_type] = {
                'packets': stats['packets'],
                'bytes': stats['bytes'],
                'speed_bps': stats['speed'],
                'percentage': 0  # 稍后计算百分比
            }

        # 计算百分比
        total_bytes = sum(stats['bytes'] for stats in business_stats.values())
        if total_bytes > 0:
            for biz_type in business_stats:
                business_stats[biz_type]['percentage'] = \
                    (business_stats[biz_type]['bytes'] / float(total_bytes)) * 100

        return Response(
            content_type='application/json',
            body=json.dumps({
                'success': True,
                'data': business_stats,
                'timestamp': time.time(),
                'total_bytes': total_bytes
            }, indent=2)
        )

    @route('traffic', '/api/v1/stats/switches', methods=['GET'])
    def get_switches(self, req, **kwargs):
        """获取已连接的交换机列表"""
        switches = []
        for dpid in self.monitor_app.datapaths:
            switch_info = {
                'id': "{:016x}".format(dpid),  # Python 2.7兼容
                'state': 'connected',
                'ports': []
            }
            # 收集该交换机的端口信息
            for (sw_dpid, port_no), (rx, tx) in self.monitor_app.prev_port_bytes.items():
                if sw_dpid == dpid:
                    switch_info['ports'].append({
                        'port_no': port_no,
                        'rx_bytes': rx,
                        'tx_bytes': tx
                    })
            switches.append(switch_info)

        return Response(
            content_type='application/json',
            body=json.dumps({
                'success': True,
                'data': switches,
                'count': len(switches),
                'timestamp': time.time()
            }, indent=2)
        )

    @route('traffic', '/api/v1/stats/summary', methods=['GET'])
    def get_summary(self, req, **kwargs):
        """获取流量统计摘要"""
        # 计算总流量
        total_rx = total_tx = 0
        for rx, tx in self.monitor_app.prev_port_bytes.values():
            total_rx += rx
            total_tx += tx

        # 计算总速率
        total_rx_speed = total_tx_speed = 0
        for stats in self.monitor_app.port_stats.values():
            total_rx_speed += stats.get('rx_speed', 0)
            total_tx_speed += stats.get('tx_speed', 0)

        # 业务类型统计
        business_count = {}
        for biz_type, stats in self.monitor_app.business_stats.items():
            if stats['packets'] > 0:
                business_count[biz_type] = stats['packets']

        summary = {
            'total_traffic': {
                'rx_bytes': total_rx,
                'tx_bytes': total_tx,
                'total_bytes': total_rx + total_tx,
                'rx_speed_bps': total_rx_speed,
                'tx_speed_bps': total_tx_speed
            },
            'device_count': {
                'switches': len(self.monitor_app.datapaths),
                'total_ports': len(self.monitor_app.prev_port_bytes)
            },
            'business_types': {
                'count': len(business_count),
                'list': list(business_count.keys()),
                'details': business_count
            },
            'monitoring': {
                'interval_seconds': 10,
                'last_update': getattr(self.monitor_app, 'last_stats_update', time.time())
            }
        }

        return Response(
            content_type='application/json',
            body=json.dumps({
                'success': True,
                'data': summary,
                'timestamp': time.time()
            }, indent=2)
        )

    @route('traffic', '/api/v1/health', methods=['GET'])
    def health_check(self, req, **kwargs):
        """健康检查端点"""
        uptime = 0
        if hasattr(self.monitor_app, 'start_time'):
            uptime = time.time() - self.monitor_app.start_time

        return Response(
            content_type='application/json',
            body=json.dumps({
                'success': True,
                'status': '运行正常',
                'service': 'SDN 流量监控系统',
                #'version': 'v5.1',
                'timestamp': time.time(),
                'uptime': uptime
            }, indent=2)
        )

    @route('traffic', '/api/v1/top_talkers', methods=['GET'])
    def get_top_talkers(self, req, **kwargs):
        """获取流量最大的前N个端口（TOP N）"""
        try:
            # 从查询参数获取n值
            query_params = req.query_string
            n_str = '10'  # 默认值
            if query_params:
                params = dict(param.split('=') for param in query_params.split('&') if '=' in param)
                n_str = params.get('n', '10')
            top_n = int(n_str)
        except (ValueError, KeyError):
            top_n = 10

        # 计算每个端口的总流量
        port_traffic = []
        for (dpid, port_no), (rx_bytes, tx_bytes) in self.monitor_app.prev_port_bytes.items():
            total_bytes = rx_bytes + tx_bytes
            port_traffic.append({
                'switch_id': "{:016x}".format(dpid),  # Python 2.7兼容
                'port': port_no,
                'total_bytes': total_bytes,
                'rx_bytes': rx_bytes,
                'tx_bytes': tx_bytes
            })

        # 按总流量排序
        port_traffic.sort(key=lambda x: x['total_bytes'], reverse=True)

        return Response(
            content_type='application/json',
            body=json.dumps({
                'success': True,
                'data': port_traffic[:top_n],
                'top_n': top_n,
                'total_ports': len(port_traffic),
                'timestamp': time.time()
            }, indent=2)
        )

# ================= 告警类 =================
class AlertManager:
    def __init__(self, logger):
        self.logger = logger
        self.alerts = []  # 告警列表
        self.port_history = defaultdict(list)      # 端口历史数据
        self.business_history = defaultdict(list)  # 业务历史数据
        self.max_history_size = 100  # 最大历史记录数

    def add_alert(self, alert_type, severity, message, details=None):
        """Add a new alert - 添加新告警"""
        alert = {
            'id': len(self.alerts) + 1,  # 告警ID
            'type': alert_type,          # 告警类型
            'severity': severity,        # 严重级别
            'message': message,          # 告警消息
            'timestamp': time.time(),    # 时间戳
            'details': details or {}     # 详细数据
        }
        self.alerts.append(alert)

        # 根据严重级别记录日志
        if severity == 'CRITICAL':
            self.logger.error("[告警-严重] {}".format(message))
        elif severity == 'ERROR':
            self.logger.error("[告警-错误] {}".format(message))
        elif severity == 'WARNING':
            self.logger.warning("[告警-警告] {}".format(message))
        else:
            self.logger.info("[告警-信息] {}".format(message))

        return alert

    def check_port_traffic(self, dpid, port_no, rx_speed, tx_speed):
        """Check port traffic for anomalies - 检查端口流量异常"""
        total_speed = rx_speed + tx_speed
        key = (dpid, port_no)

        # 存储历史数据
        self.port_history[key].append({
            'timestamp': time.time(),
            'rx_speed': rx_speed,
            'tx_speed': tx_speed,
            'total_speed': total_speed
        })

        # 只保留最近的历史数据
        if len(self.port_history[key]) > self.max_history_size:
            self.port_history[key] = self.port_history[key][-self.max_history_size:]

        # 检查高流量
        if total_speed > ALERT_CONFIG['high_traffic']['threshold_bps']:
            speed_mbps = total_speed / 1000000.0
            self.add_alert(
                'high_traffic',
                ALERT_CONFIG['high_traffic']['severity'],
                ALERT_CONFIG['high_traffic']['message'].format(
                    port=port_no,
                    switch="{:016x}".format(dpid),
                    speed=speed_mbps
                ),
                {
                    'switch_id': dpid,
                    'port': port_no,
                    'speed_bps': total_speed,
                    'speed_mbps': speed_mbps,
                    'threshold_bps': ALERT_CONFIG['high_traffic']['threshold_bps']
                }
            )

        # 检查流量突发
        if len(self.port_history[key]) > 10:
            recent_speeds = [h['total_speed'] for h in self.port_history[key][-10:]]
            avg_speed = sum(recent_speeds) / len(recent_speeds)

            if avg_speed > 0 and total_speed > avg_speed * ALERT_CONFIG['traffic_burst']['threshold_multiplier']:
                self.add_alert(
                    'traffic_burst',
                    ALERT_CONFIG['traffic_burst']['severity'],
                    ALERT_CONFIG['traffic_burst']['message'].format(
                        port=port_no,
                        switch="{:016x}".format(dpid),
                        current_speed=total_speed / 1000000.0,
                        normal_speed=avg_speed / 1000000.0
                    ),
                    {
                        'switch_id': dpid,
                        'port': port_no,
                        'current_speed_bps': total_speed,
                        'normal_speed_bps': avg_speed,
                        'multiplier': total_speed / avg_speed
                    }
                )

        return total_speed

    def check_port_errors(self, dpid, port_no, rx_errors, tx_errors):
        """Check port errors - 检查端口错误"""
        total_errors = rx_errors + tx_errors

        if total_errors > ALERT_CONFIG['port_errors']['error_threshold']:
            self.add_alert(
                'port_errors',
                ALERT_CONFIG['port_errors']['severity'],
                ALERT_CONFIG['port_errors']['message'].format(
                    port=port_no,
                    switch="{:016x}".format(dpid),
                    errors=total_errors
                ),
                {
                    'switch_id': dpid,
                    'port': port_no,
                    'rx_errors': rx_errors,
                    'tx_errors': tx_errors,
                    'total_errors': total_errors
                }
            )

    def check_business_traffic(self, business_type, current_speed):
        """Check business traffic for anomalies - 检查业务流量异常"""
        # 获取该业务的正常流量
        normal_traffic = 0
        if business_type in BUSINESS_RULES and 'normal_traffic_bps' in BUSINESS_RULES[business_type]:
            normal_traffic = BUSINESS_RULES[business_type]['normal_traffic_bps']

        if normal_traffic > 0 and current_speed > normal_traffic * ALERT_CONFIG['business_anomaly'][
            'threshold_multiplier']:
            biz_info = BUSINESS_RULES.get(business_type, {'name': business_type})
            self.add_alert(
                'business_anomaly',
                ALERT_CONFIG['business_anomaly']['severity'],
                ALERT_CONFIG['business_anomaly']['message'].format(
                    biz_info['name'],
                    current_speed / 1000000.0,
                    normal_traffic / 1000000.0
                ),
                {
                    'business_type': business_type,
                    'business_name': biz_info['name'],
                    'current_speed_bps': current_speed,
                    'normal_speed_bps': normal_traffic,
                    'multiplier': current_speed / normal_traffic
                }
            )


# ================= 主应用类（包含告警功能） =================
class MyMonitorEnhanced(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # 启用REST API
    _CONTEXTS = {
        'wsgi': WSGIApplication
    }

    def __init__(self, *args, **kwargs):
        super(MyMonitorEnhanced, self).__init__(*args, **kwargs)

        # 存储启动时间
        self.start_time = time.time()
        self.last_stats_update = time.time()

        # 基础数据结构
        self.datapaths = {}          # 数据路径字典
        self.port_stats = {}         # 端口统计
        self.flow_stats = {}         # 流表统计
        self.prev_port_bytes = {}    # 前一个端口字节数
        self.prev_flow_bytes = {}    # 前一个流字节数

        # 业务数据结构
        self.business_stats = defaultdict(
            lambda: {'packets': 0, 'bytes': 0, 'speed': 0.0}  # 业务统计: 包数, 字节数, 速度
        )
        self.prev_business_bytes = defaultdict(int)  # 前一个业务字节数

        # 告警系统
        self.alert_manager = AlertManager(self.logger)
        self.alerts = self.alert_manager.alerts  # 供API访问的告警列表

        # 启动监控线程
        self.monitor_thread = hub.spawn(self._monitor)

        # ========== 初始化REST API ==========
        wsgi = kwargs.get('wsgi')
        if wsgi:
            # 注册控制器
            wsgi.register(
                TrafficMonitorAPIController,
                {'monitor_app': self}
            )
            self.logger.info("REST API 已启用，监听端口: 8080")  # REST API已启用，监听端口:8080
        else:
            self.logger.warning("REST API 未启用")  # REST API未启用

        self.logger.info("SDN流量监控器已启动")
        self.logger.info("告警系统已初始化，包含 {} 种告警类型".format(len(ALERT_CONFIG)))     # 告警系统初始化，包含{}种告警类型
        self.logger.info("启动时间: {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))    # 启动时间

        self.logger.info("流量告警阈值配置:")
        for alert_type, config in ALERT_CONFIG.items():
            if 'threshold_bps' in config:
                self.logger.info("  {}: {:.1f} Mbps".format(
                    alert_type, config['threshold_bps'] / 1000000.0
                ))
            elif 'threshold_percent' in config:
                self.logger.info("  {}: {}%".format(
                    alert_type, config['threshold_percent']
                ))

        self.logger.info("业务规则已加载:")
        for biz_type, rule in BUSINESS_RULES.items():
            self.logger.info("  {}: {} (优先级: {})".format(
                rule['name'], rule['description'], rule['priority']
            ))

    # ================= Switch Features 交换机特性 =================
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser

        # table-miss流表项
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofp.OFPP_CONTROLLER,
                ofp.OFPCML_NO_BUFFER
            )
        ]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=0,
            match=match,
            instructions=inst
        )
        dp.send_msg(mod)

        self.logger.info("交换机已连接: %016x" % int(dp.id))  # 交换机连接

    # ================= State Change 状态变更 =================
    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        dp = ev.datapath
        self.logger.info("===== 交换机状态变更 =====")
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
            # 安全转换dp.id为整数进行格式化
            try:
                switch_id = int(dp.id) if dp.id is not None else 0
                self.logger.info("交换机 %016x 进入主状态" % switch_id)
            except (ValueError, TypeError):
                self.logger.info("交换机 %s 进入主状态" % str(dp.id))
        elif ev.state == DEAD_DISPATCHER:
            # 调试：查看dp.id的实际类型和值
            print("DEBUG: dp.id =", dp.id, "type =", type(dp.id))

            # 安全处理：检查dp.id是否存在再pop
            if dp.id is not None and dp.id in self.datapaths:
                self.datapaths.pop(dp.id, None)

            # 记录断开连接日志
            if dp.id is not None:
                try:
                    # 尝试十六进制格式化
                    switch_id = int(dp.id) if not isinstance(dp.id, (int, long)) else dp.id
                    log_msg = "交换机 %016x 断开连接" % switch_id
                except (ValueError, TypeError):
                    # 如果转换失败，使用字符串
                    log_msg = "交换机 %s 断开连接" % str(dp.id)
            else:
                log_msg = "未知交换机断开连接"

            self.logger.warning(log_msg)

            # 添加交换机断开连接告警
            if dp.id is not None:
                try:
                    switch_id = int(dp.id) if not isinstance(dp.id, (int, long)) else dp.id
                    alert_msg = "交换机 %016x 已从控制器断开连接" % switch_id
                except (ValueError, TypeError):
                    alert_msg = "交换机 %s 已从控制器断开连接" % str(dp.id)

                self.alert_manager.add_alert(
                    'switch_disconnect',
                    'WARNING',
                    alert_msg,
                    {'switch_id': dp.id, 'state': 'DEAD'}
                )

    # ================= Periodic Monitoring 定期监控 =================
    def _monitor(self):
        self.logger.info("===== 监控线程启动 =====")
        """Periodic monitoring thread - 定期监控线程"""
        while True:
            self.last_stats_update = time.time()
            for dp in self.datapaths.values():
                self._request_stats(dp)
            hub.sleep(10)

    def _request_stats(self, dp):
        """Request statistics - 请求统计信息"""
        self.logger.debug("向交换机 {} 请求统计信息".format(dp.id))
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        dp.send_msg(parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))  # 请求端口统计
        dp.send_msg(parser.OFPFlowStatsRequest(dp))  # 请求流表统计

    # ================= Port Statistics with Alerts 带告警的端口统计 =================
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        self.logger.info("===== 端口统计 =====")
        self.logger.info("交换机: {:016x}".format(dpid))
        #self.logger.info("端口统计 - 交换机 {:016x}".format(dpid))

        for stat in ev.msg.body:
            if stat.port_no > 0xffffff00:
                continue  # 跳过虚拟端口

            key = (dpid, stat.port_no)
            rx = stat.rx_bytes
            tx = stat.tx_bytes

            prev_rx, prev_tx = self.prev_port_bytes.get(key, (rx, tx))
            rx_speed = (rx - prev_rx) / 10.0  # 接收速度(B/s)
            tx_speed = (tx - prev_tx) / 10.0  # 发送速度(B/s)

            self.prev_port_bytes[key] = (rx, tx)
            self.port_stats[key] = {
                'rx_speed': rx_speed,
                'tx_speed': tx_speed,
                'rx_packets': stat.rx_packets,
                'tx_packets': stat.tx_packets,
                'rx_errors': stat.rx_errors,
                'tx_errors': stat.tx_errors
            }

            # 检查告警
            total_speed = self.alert_manager.check_port_traffic(dpid, stat.port_no, rx_speed, tx_speed)
            self.alert_manager.check_port_errors(dpid, stat.port_no, stat.rx_errors, stat.tx_errors)

            # 检查端口利用率(假设为1G端口)
            if total_speed > 0:
                utilization = (total_speed / (1000.0 * 1000.0 * 1000.0)) * 100  # 利用率百分比
                if utilization > ALERT_CONFIG['port_utilization']['threshold_percent']:
                    self.alert_manager.add_alert(
                        'port_utilization',
                        ALERT_CONFIG['port_utilization']['severity'],
                        ALERT_CONFIG['port_utilization']['message'].format(
                            port=stat.port_no,
                            switch="{:016x}".format(dpid),
                            utilization=utilization
                        ),
                        {
                            'switch_id': dpid,
                            'port': stat.port_no,
                            'utilization_percent': utilization,
                            'threshold_percent': ALERT_CONFIG['port_utilization']['threshold_percent']
                        }
                    )

            # 原始端口统计输出
            self.logger.info(
                    "端口 {}: 接收 数据包:{} 字节:{} 速度:{:.2f}B/s | "
                    "发送 数据包:{} 字节:{} 速度:{:.2f}B/s".format(
                    stat.port_no,
                    stat.rx_packets, rx, rx_speed,
                    stat.tx_packets, tx, tx_speed
                )
            )

    def _print_business_stats(self):
        """Print business statistics - 打印业务统计信息"""
        self.logger.info("===== 业务流量统计 =====")
        for biz, data in self.business_stats.items():
            if data['packets'] > 0:
                biz_info = BUSINESS_RULES.get(biz, {'name': biz})
                self.logger.info(
                     "[{}] 数据包:{} 字节:{} 速度:{:.2f} B/s".format(
                        biz_info['name'],
                        data['packets'],
                        data['bytes'],
                        data['speed']
                    )
                )

                # 检查业务流量异常
                if data['speed'] > 0:
                    self.alert_manager.check_business_traffic(biz, data['speed'])

    # ================= Flow Statistics 流表统计 =================
    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        self.logger.info("===== 流表统计 =====")
        self.logger.info("[流表统计] 交换机={}, 项数={}".format(dpid, len(ev.msg.body)))
        # 临时存储
        current_business_bytes = defaultdict(int)

        for stat in ev.msg.body:
            match = stat.match
            packets = stat.packet_count
            bytes_now = stat.byte_count

            # 业务类型识别
            biz_type = "OTHER"
            biz_name = "Other"

            if 'ip_proto' in match:
                if match['ip_proto'] == 1:
                    biz_type = "ICMP"
                    biz_name = "ICMP"
                elif match['ip_proto'] == 6:
                    if 'tcp_dst' in match and match['tcp_dst'] == 80:
                        biz_type = "HTTP"
                        biz_name = "HTTP"
                    else:
                        biz_type = "TCP"
                        biz_name = "TCP"
                elif match['ip_proto'] == 17:
                    biz_type = "UDP"
                    biz_name = "UDP"

            # 累计业务流量
            self.business_stats[biz_type]['packets'] += packets
            self.business_stats[biz_type]['bytes'] += bytes_now
            current_business_bytes[biz_type] += bytes_now

            # 原始流表输出
            self.logger.info(
                 "流表匹配:{} 数据包:{} 字节:{}".format(
                    self._format_match(match), packets, bytes_now
                )
            )

        # 计算业务速度
        for biz, cur_bytes in current_business_bytes.items():
            prev = self.prev_business_bytes[biz]
            self.business_stats[biz]['speed'] = (cur_bytes - prev) / 10.0
            self.prev_business_bytes[biz] = cur_bytes

        # 打印业务统计
        self._print_business_stats()

    def _format_match(self, match):
        """Format match fields for display - 格式化匹配字段用于显示"""
        formatted = []
        for key, value in match.items():
            if key == 'in_port':
                formatted.append("in:{}".format(value))
            elif key == 'eth_type':
                formatted.append("eth_type=0x{:04x}".format(value))
            elif key == 'ipv4_src':
                formatted.append("src_ip={}".format(value))
            elif key == 'ipv4_dst':
                formatted.append("dst_ip={}".format(value))
            elif key == 'ip_proto':
                proto_name = {1: 'ICMP', 6: 'TCP', 17: 'UDP'}.get(value, str(value))
                formatted.append("proto={}".format(proto_name))
            elif key in ['tcp_src', 'tcp_dst', 'udp_src', 'udp_dst']:
                formatted.append("{}={}".format(key, value))

        if not formatted:
            return "table-miss"  # 表缺失

        return ", ".join(formatted)

    # ================= PacketIn Handler 数据包进入处理器 =================
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # 忽略LLDP
        if eth.ethertype == 0x88cc:
            return

        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        if ip_pkt:
            src = ip_pkt.src
            dst = ip_pkt.dst

            # 构建基础匹配
            match_kwargs = {
                'in_port': in_port,
                'eth_type': 0x0800,  # IPv4
                'ipv4_src': src,
                'ipv4_dst': dst
            }

            # ICMP
            if pkt.get_protocol(icmp.icmp):
                self.logger.info(
                    "[业务流量]: ICMP {} -> {}".format(src, dst)  # 业务流量: ICMP
                )
                match_kwargs['ip_proto'] = 1

            # TCP
            tcp_pkt = pkt.get_protocol(tcp.tcp)
            if tcp_pkt:
                sport = tcp_pkt.src_port
                dport = tcp_pkt.dst_port
                match_kwargs['ip_proto'] = 6
                match_kwargs['tcp_src'] = sport
                match_kwargs['tcp_dst'] = dport

                if dport == 80 or sport == 80:
                    self.logger.info(
                        "[业务流量]: HTTP {}:{} -> {}:{}".format(
                            src, sport, dst, dport
                        )
                    )
                else:
                    self.logger.info(
                        "[业务流量]: TCP {}:{} -> {}:{}".format(
                            src, sport, dst, dport
                        )
                    )

            # UDP
            udp_pkt = pkt.get_protocol(udp.udp)
            if udp_pkt:
                match_kwargs['ip_proto'] = 17
                match_kwargs['udp_src'] = udp_pkt.src_port
                match_kwargs['udp_dst'] = udp_pkt.dst_port

                self.logger.info(
                    "[业务流量]: UDP {}:{} -> {}:{}".format(
                        src, udp_pkt.src_port,
                        dst, udp_pkt.dst_port
                    )
                )

            # 安装精确的业务流表
            match = parser.OFPMatch(**match_kwargs)

            actions = [
                parser.OFPActionOutput(ofp.OFPP_FLOOD)  # 泛洪
            ]

            inst = [
                parser.OFPInstructionActions(
                    ofp.OFPIT_APPLY_ACTIONS,
                    actions
                )
            ]

            flow_mod = parser.OFPFlowMod(
                datapath=dp,
                priority=10,
                match=match,
                instructions=inst,
                idle_timeout=30  # 空闲超时30秒
            )

            dp.send_msg(flow_mod)

        # 第一个数据包仍然使用PacketOut
        out = parser.OFPPacketOut(
            datapath=dp,
            buffer_id=ofp.OFP_NO_BUFFER,
            in_port=in_port,
            actions=[parser.OFPActionOutput(ofp.OFPP_FLOOD)],
            data=msg.data
        )
        dp.send_msg(out)