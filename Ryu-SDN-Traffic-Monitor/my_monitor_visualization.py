#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SDN流量监控可视化模块 - Python 2.7兼容版
确保API路径与前端匹配
"""
from flask import Flask, render_template, jsonify, request, send_from_directory
import json
import time
import threading
from datetime import datetime
import sys
import os
import random

# 检测Python版本
PYTHON_VERSION = sys.version_info[0]
print("Python版本: {}.{}.{}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2]))

# 导入requests或使用urllib2
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    if PYTHON_VERSION == 2:
        import urllib2
    else:
        import urllib.request as urllib2

app = Flask(__name__,
            static_folder='static',
            template_folder='templates')

# ================= 数据源配置 =================
USE_MOCK_DATA = False
RYU_API_URL = "http://localhost:8080"
RYU_API_VERSION = "v1"


# ================= 模拟数据生成器 =================
class MockDataGenerator:
    """生成模拟数据，用于在没有Ryu API时使用"""

    def __init__(self):
        self.switches = []
        self.alerts = []
        self.port_history = {}
        self.business_history = {}
        self.summary = {}
        self.start_time = time.time()

        # 初始化模拟数据
        self._init_mock_data()

        # 启动数据更新线程
        update_thread = threading.Thread(target=self._update_loop)
        update_thread.daemon = True
        update_thread.start()

    def _init_mock_data(self):
        """初始化模拟数据"""
        # 模拟交换机
        self.switches = [
            {
                'id': '0000000000000001',
                'state': 'connected',
                'ports': [
                    {'port_no': 1, 'rx_bytes': 1024000, 'tx_bytes': 512000},
                    {'port_no': 2, 'rx_bytes': 2048000, 'tx_bytes': 1024000},
                    {'port_no': 3, 'rx_bytes': 3072000, 'tx_bytes': 1536000},
                ]
            },
            {
                'id': '0000000000000002',
                'state': 'connected',
                'ports': [
                    {'port_no': 1, 'rx_bytes': 512000, 'tx_bytes': 256000},
                    {'port_no': 2, 'rx_bytes': 1024000, 'tx_bytes': 512000},
                ]
            }
        ]

        # 初始化端口历史数据
        for switch in self.switches:
            for port in switch['ports']:
                key = "{}_{}".format(switch['id'], port['port_no'])
                self.port_history[key] = {
                    'timestamps': [],
                    'rx_speeds': [],
                    'tx_speeds': []
                }

        # 初始化业务数据
        self.business_history = {
            'ICMP': {'timestamps': [], 'speeds': []},
            'HTTP': {'timestamps': [], 'speeds': []},
            'TCP': {'timestamps': [], 'speeds': []},
            'UDP': {'timestamps': [], 'speeds': []}
        }

        # 初始化告警
        self.alerts = [
            {
                'id': 1,
                'type': 'high_traffic',
                'severity': 'WARNING',
                'message': '检测到交换机 0000000000000001 端口 1 流量过高: 15.23 Mbps',
                'timestamp': time.time() - 300,
                'details': {'switch_id': '0000000000000001', 'port': 1}
            },
            {
                'id': 2,
                'type': 'port_utilization',
                'severity': 'INFO',
                'message': '交换机 0000000000000001 端口 2 利用率: 65.5%',
                'timestamp': time.time() - 600,
                'details': {'switch_id': '0000000000000001', 'port': 2}
            }
        ]

        # 初始化摘要
        self.summary = {
            'total_traffic': {
                'rx_bytes': 15000000,
                'tx_bytes': 8000000,
                'total_bytes': 23000000,
                'rx_speed_bps': 5000000,
                'tx_speed_bps': 3000000
            },
            'device_count': {
                'switches': 2,
                'total_ports': 5
            },
            'business_types': {
                'count': 4,
                'list': ['ICMP', 'HTTP', 'TCP', 'UDP'],
                'details': {'ICMP': 1000, 'HTTP': 5000, 'TCP': 3000, 'UDP': 2000}
            }
        }

    def _update_loop(self):
        """模拟数据更新循环"""
        while True:
            self._update_mock_data()
            time.sleep(5)  # 每5秒更新一次

    def _update_mock_data(self):
        """更新模拟数据"""
        current_time = datetime.now().strftime('%H:%M:%S')

        # 更新端口数据
        for switch in self.switches:
            for port in switch['ports']:
                key = "{}_{}".format(switch['id'], port['port_no'])

                # 添加新数据点
                if len(self.port_history[key]['timestamps']) >= 20:
                    self.port_history[key]['timestamps'].pop(0)
                    self.port_history[key]['rx_speeds'].pop(0)
                    self.port_history[key]['tx_speeds'].pop(0)

                # 生成随机速度
                rx_speed = random.randint(50, 200)  # Mbps
                tx_speed = random.randint(30, 150)  # Mbps

                self.port_history[key]['timestamps'].append(current_time)
                self.port_history[key]['rx_speeds'].append(rx_speed)
                self.port_history[key]['tx_speeds'].append(tx_speed)

        # 更新业务数据
        for biz in self.business_history.keys():
            if len(self.business_history[biz]['timestamps']) >= 20:
                self.business_history[biz]['timestamps'].pop(0)
                self.business_history[biz]['speeds'].pop(0)

            speed = random.randint(10, 100)  # Mbps
            self.business_history[biz]['timestamps'].append(current_time)
            self.business_history[biz]['speeds'].append(speed)

        # 更新摘要数据
        self.summary['total_traffic']['rx_bytes'] += random.randint(1000, 10000)
        self.summary['total_traffic']['tx_bytes'] += random.randint(500, 5000)
        self.summary['total_traffic']['rx_speed_bps'] = random.randint(4000000, 6000000)
        self.summary['total_traffic']['tx_speed_bps'] = random.randint(2000000, 4000000)


# ================= 真实数据获取器 =================
def fetch_ryu_data(endpoint):
    """从Ryu REST API获取数据"""
    try:
        url = "{}/api/{}/{}".format(RYU_API_URL, RYU_API_VERSION, endpoint)
        if REQUESTS_AVAILABLE:
            response = requests.get(url, timeout=3)
            if response.status_code == 200:
                return response.json()
        else:
            # 使用urllib2 (Python 2)
            req = urllib2.Request(url)
            response = urllib2.urlopen(req, timeout=3)
            return json.loads(response.read())
    except Exception as e:
        print("Ryu API请求失败 {}: {}".format(url, e))
        return None


# 初始化数据源
mock_generator = MockDataGenerator()
print("初始化模拟数据生成器")


# ================= Flask路由 =================
@app.route('/')
def index():
    """主页面"""
    return render_template('index.html')


@app.route('/api/summary')
def get_summary():
    """获取摘要数据"""
    try:
        # 尝试从Ryu获取真实数据
        ryu_data = fetch_ryu_data('stats/summary')
        if ryu_data and ryu_data.get('success'):
            return jsonify(ryu_data)
    except Exception as e:
        print("获取摘要数据失败: {}".format(e))

    # 使用模拟数据
    return jsonify({
        'success': True,
        'data': {
            'summary': mock_generator.summary
        }
    })


@app.route('/api/switches')
def get_switches():
    """获取交换机数据"""
    try:
        ryu_data = fetch_ryu_data('stats/switches')
        if ryu_data and ryu_data.get('success'):
            return jsonify(ryu_data)
    except Exception as e:
        print("获取交换机数据失败: {}".format(e))

    return jsonify({
        'success': True,
        'data': mock_generator.switches
    })


@app.route('/api/alerts')
def get_alerts():
    """获取告警数据"""
    return jsonify({
        'success': True,
        'data': mock_generator.alerts
    })


@app.route('/api/business-distribution')
def get_business_distribution():
    """获取业务分布数据"""
    try:
        ryu_data = fetch_ryu_data('stats/business')
        if ryu_data and ryu_data.get('success'):
            # 转换数据格式
            business_stats = ryu_data['data']
            business_data = []
            for biz_type, stats in business_stats.items():
                business_data.append({
                    'name': biz_type,
                    'value': stats.get('percentage', 0),
                    'color': get_random_color()
                })

            return jsonify({
                'success': True,
                'data': business_data
            })
    except Exception as e:
        print("获取业务分布数据失败: {}".format(e))

    # 使用模拟数据
    business_data = [
        {'name': 'ICMP', 'value': random.randint(10, 30), 'color': '#FF6B6B'},
        {'name': 'HTTP', 'value': random.randint(30, 50), 'color': '#4ECDC4'},
        {'name': 'TCP', 'value': random.randint(20, 40), 'color': '#FFD166'},
        {'name': 'UDP', 'value': random.randint(10, 30), 'color': '#06D6A0'}
    ]

    return jsonify({
        'success': True,
        'data': business_data
    })


@app.route('/api/business-trend')
def get_business_trend():
    """获取业务趋势数据"""
    # 生成时间戳
    timestamps = []
    for i in range(10):
        timestamps.append("{}:{:02d}".format(datetime.now().hour, i * 6))

    # 生成业务数据
    series = [
        {
            'name': 'ICMP',
            'data': [random.randint(5, 20) for _ in range(10)],
            'color': '#FF6B6B'
        },
        {
            'name': 'HTTP',
            'data': [random.randint(20, 50) for _ in range(10)],
            'color': '#4ECDC4'
        },
        {
            'name': 'TCP',
            'data': [random.randint(15, 40) for _ in range(10)],
            'color': '#FFD166'
        },
        {
            'name': 'UDP',
            'data': [random.randint(10, 30) for _ in range(10)],
            'color': '#06D6A0'
        }
    ]

    return jsonify({
        'success': True,
        'data': {
            'timestamps': timestamps,
            'series': series
        }
    })


@app.route('/api/top-talkers')
def get_top_talkers():
    """获取流量最大的端口"""
    try:
        ryu_data = fetch_ryu_data('stats/top_talkers?n=10')
        if ryu_data and ryu_data.get('success'):
            return jsonify(ryu_data)
    except Exception as e:
        print("获取TOP N数据失败: {}".format(e))

    # 使用模拟数据
    top_ports = []
    for switch in mock_generator.switches:
        for port in switch['ports']:
            total_bytes = port['rx_bytes'] + port['tx_bytes']
            top_ports.append({
                'switch_id': switch['id'],
                'port': port['port_no'],
                'total_bytes': total_bytes,
                'rx_bytes': port['rx_bytes'],
                'tx_bytes': port['tx_bytes']
            })

    # 排序
    top_ports.sort(key=lambda x: x['total_bytes'], reverse=True)
    top_ports = top_ports[:10]

    return jsonify({
        'success': True,
        'data': top_ports
    })


@app.route('/api/port-history/<switch_id>/<port_no>')
def get_port_history(switch_id, port_no):
    """获取端口历史数据"""
    key = "{}_{}".format(switch_id, port_no)

    if key in mock_generator.port_history:
        history = mock_generator.port_history[key]
        data = {
            'timestamps': history['timestamps'][-10:],
            'rx_speeds': history['rx_speeds'][-10:],
            'tx_speeds': history['tx_speeds'][-10:]
        }
    else:
        # 生成模拟数据
        timestamps = ["{}:{:02d}".format(datetime.now().hour, i) for i in range(10)]
        data = {
            'timestamps': timestamps,
            'rx_speeds': [random.randint(50, 200) for _ in range(10)],
            'tx_speeds': [random.randint(30, 150) for _ in range(10)]
        }

    return jsonify({
        'success': True,
        'data': data
    })


@app.route('/api/health')
def health():
    """健康检查"""
    try:
        # 测试Ryu连接
        ryu_health = fetch_ryu_data('health')
        if ryu_health and ryu_health.get('success'):
            data_source = 'Ryu API'
        else:
            data_source = '模拟数据'
    except Exception as e:
        data_source = '模拟数据'
        print("健康检查连接Ryu失败: {}".format(e))

    return jsonify({
        'success': True,
        'status': 'running',
        'service': 'SDN流量监控可视化',
        'data_source': data_source,
        'timestamp': time.time()
    })


@app.route('/static/<path:filename>')
def serve_static(filename):
    """提供静态文件"""
    return send_from_directory('static', filename)


# ================= 工具函数 =================
def get_random_color():
    """获取随机颜色"""
    colors = [
        '#FF6B6B', '#4ECDC4', '#FFD166', '#06D6A0',
        '#118AB2', '#EF476F', '#9B5DE5', '#00BBF9',
        '#00F5D4', '#FF97B7', '#A663CC', '#B5E48C'
    ]
    return random.choice(colors)


# ================= 主函数 =================
if __name__ == '__main__':
    print("=" * 60)
    print("🌐 SDN流量监控可视化系统 - Python 2.7兼容版")
    print("=" * 60)
    print("访问地址: http://127.0.0.1:5000")
    print("Ryu API地址: {}".format(RYU_API_URL))
    print("当前目录: {}".format(os.getcwd()))
    print("静态文件夹: {}".format(app.static_folder))
    print("模板文件夹: {}".format(app.template_folder))

    # 检查静态文件和模板
    if os.path.exists(app.template_folder):
        print("模板文件存在: 是")
    else:
        print("模板文件存在: 否 - 请确保templates文件夹存在")

    print("=" * 60)
    print("测试端点:")
    print("  主页: http://127.0.0.1:5000/")
    print("  健康检查: http://127.0.0.1:5000/api/health")
    print("  摘要数据: http://127.0.0.1:5000/api/summary")
    print("  交换机列表: http://127.0.0.1:5000/api/switches")
    print("=" * 60)

    # 启动Flask应用
    try:
        app.run(host='0.0.0.0', port=5000, debug=True, threaded=True, use_reloader=False)
    except Exception as e:
        print("启动Flask应用失败: {}".format(e))
        print("请检查端口5000是否被占用: lsof -i :5000")