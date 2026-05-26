/**
 * SDN流量监控仪表板 - 兼容版JavaScript模块
 */

class SDNDashboard {
    constructor() {
        this.config = {
            apiBase: '/api',
            refreshInterval: 10000
        };
        this.charts = {};
        this.data = {};
        this.selectedPort = null;
        this.autoRefresh = true;
        this.refreshTimer = null;

        this.initialize();
    }

    initialize() {
        console.log('初始化SDN流量监控仪表板...');
        this.initCharts();
        this.bindEvents();
        this.loadAllData();
        this.startAutoRefresh();
    }

    initCharts() {
        // 检查ECharts是否已加载
        if (typeof echarts === 'undefined') {
            console.error('ECharts未加载！请检查ECharts脚本是否正确引入。');
            setTimeout(this.initCharts.bind(this), 1000); // 1秒后重试
            return;
        }

        console.log('ECharts已加载，初始化图表...');

        // 初始化ECharts图表
        this.charts.businessPie = echarts.init(document.getElementById('businessPieChart'));
        this.charts.businessTrend = echarts.init(document.getElementById('businessTrendChart'));
        this.charts.portDetail = echarts.init(document.getElementById('portDetailChart'));

        // 设置默认配置
        this.setDefaultChartOptions();

        // 监听窗口大小变化
        var self = this; // 保存this引用
        window.addEventListener('resize', function() {
            Object.values(self.charts).forEach(function(chart) {
                chart.resize();
            });
        });
    }

    setDefaultChartOptions() {
        // 业务分布饼图配置
        this.charts.businessPie.setOption({
            tooltip: {
                trigger: 'item',
                formatter: '{a} <br/>{b}: {c} Mbps ({d}%)'
            },
            legend: {
                orient: 'vertical',
                right: 10,
                top: 'center'
            },
            series: [{
                name: '业务流量分布',
                type: 'pie',
                radius: '70%',
                center: ['40%', '50%'],
                data: [],
                emphasis: {
                    itemStyle: {
                        shadowBlur: 10,
                        shadowOffsetX: 0,
                        shadowColor: 'rgba(0, 0, 0, 0.5)'
                    }
                }
            }]
        });

        // 业务趋势图配置
        this.charts.businessTrend.setOption({
            tooltip: {
                trigger: 'axis'
            },
            legend: {
                data: []
            },
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                containLabel: true
            },
            xAxis: {
                type: 'category',
                boundaryGap: false,
                data: []
            },
            yAxis: {
                type: 'value',
                name: '流量 (Mbps)'
            },
            series: []
        });

        // 端口详情图配置
        this.charts.portDetail.setOption({
            tooltip: {
                trigger: 'axis'
            },
            legend: {
                data: ['接收流量', '发送流量']
            },
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                containLabel: true
            },
            xAxis: {
                type: 'category',
                data: []
            },
            yAxis: {
                type: 'value',
                name: '流量 (Mbps)'
            },
            series: [
                {
                    name: '接收流量',
                    type: 'line',
                    data: [],
                    smooth: true
                },
                {
                    name: '发送流量',
                    type: 'line',
                    data: [],
                    smooth: true
                }
            ]
        });
    }

    bindEvents() {
        var self = this;

        // 刷新按钮
        var refreshBtn = document.getElementById('refreshBtn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', function() {
                self.refreshData();
                self.showToast('正在刷新数据...', 'info');
            });
        }

        // 自动刷新开关
        var autoRefreshToggle = document.getElementById('autoRefreshToggle');
        if (autoRefreshToggle) {
            autoRefreshToggle.addEventListener('click', function() {
                self.autoRefresh = !self.autoRefresh;
                var icon = document.getElementById('autoRefreshIcon');
                if (icon) {
                    if (self.autoRefresh) {
                        self.startAutoRefresh();
                        icon.className = 'fas fa-redo';
                        self.showToast('已启用自动刷新', 'success');
                    } else {
                        self.stopAutoRefresh();
                        icon.className = 'fas fa-pause';
                        self.showToast('已禁用自动刷新', 'warning');
                    }
                }
            });
        }

        // 端口选择器
        var portSelector = document.getElementById('portSelector');
        if (portSelector) {
            portSelector.addEventListener('change', function(e) {
                self.selectedPort = e.target.value;
                if (self.selectedPort) {
                    self.loadPortHistory(self.selectedPort);
                }
            });
        }
    }

    loadAllData() {
        this.showLoading();

        // 并行加载所有数据
        var self = this;
        Promise.all([
            this.fetchData('summary'),
            this.fetchData('switches'),
            this.fetchData('alerts'),
            this.fetchData('business-distribution'),
            this.fetchData('business-trend'),
            this.fetchData('top-talkers'),
            this.fetchData('health')
        ]).then(function(results) {
            var summary = results[0];
            var switches = results[1];
            var alerts = results[2];
            var distribution = results[3];
            var trend = results[4];
            var topTalkers = results[5];
            var health = results[6];

            self.hideLoading();

            self.updateSummary(summary);
            self.updateBusinessDistribution(distribution);
            self.updateBusinessTrend(trend);
            self.updateSwitches(switches);
            self.updateAlerts(alerts);
            self.updateTopTalkers(topTalkers);
            self.updateHealthStatus(health);
            self.updatePortSelector(switches);
            self.updateLastUpdateTime();

            console.log('数据加载完成');
        }).catch(function(error) {
            console.error('数据加载失败:', error);
            self.hideLoading();
            self.showToast('数据加载失败，请检查网络连接', 'danger');
        });
    }

    fetchData(endpoint) {
        var url = this.config.apiBase + '/' + endpoint;
        console.log('请求数据:', url);

        return fetch(url)
            .then(function(response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.json();
            })
            .then(function(data) {
                if (!data.success) {
                    throw new Error(data.error || '请求失败');
                }
                return data;
            });
    }

    updateSummary(data) {
        console.log('updateSummary接收到数据:', data);

        if (!data || !data.data) {
            console.error('updateSummary: 数据格式错误', data);
            return;
        }

        // 兼容不同的数据结构
        var summary;
        if (data.data.summary) {
            summary = data.data.summary;
        } else {
            summary = data.data;
        }

        console.log('处理后的summary:', summary);
        this.updateSummaryCards(summary);
        this.data.summary = summary;
    }

    updateSummaryCards(summary) {
        console.log('updateSummaryCards被调用，参数:', summary);

        var container = document.getElementById('summaryCards');
        if (!container) {
            console.error('找不到summaryCards容器元素');
            return;
        }

        // 使用传统的空值检查替代可选链操作符
        var total_traffic = summary.total_traffic || {};
        var device_count = summary.device_count || {};
        var business_types = summary.business_types || {};

        var cards = [
            {
                icon: 'fas fa-exchange-alt',
                value: this.formatBytes(total_traffic.total_bytes || 0),
                label: '总流量',
                color: 'primary'
            },
            {
                icon: 'fas fa-server',
                value: device_count.switches || 0,
                label: '交换机数量',
                color: 'success'
            },
            {
                icon: 'fas fa-plug',
                value: device_count.total_ports || 0,
                label: '总端口数',
                color: 'info'
            },
            {
                icon: 'fas fa-project-diagram',
                value: business_types.count || 0,
                label: '业务类型',
                color: 'warning'
            },
            {
                icon: 'fas fa-download',
                value: this.formatSpeed(total_traffic.rx_speed_bps || 0),
                label: '总接收速度',
                color: 'primary'
            },
            {
                icon: 'fas fa-upload',
                value: this.formatSpeed(total_traffic.tx_speed_bps || 0),
                label: '总发送速度',
                color: 'danger'
            }
        ];

        var html = '';
        for (var i = 0; i < cards.length; i++) {
            var card = cards[i];
            html += '<div class="col-md-4 col-lg-2">' +
                    '<div class="stat-card">' +
                    '<div class="card-icon text-' + card.color + '">' +
                    '<i class="' + card.icon + '"></i>' +
                    '</div>' +
                    '<div class="card-value">' + card.value + '</div>' +
                    '<div class="card-label">' + card.label + '</div>' +
                    '</div>' +
                    '</div>';
        }

        container.innerHTML = html;
        console.log('summaryCards已更新');
    }

    updateBusinessDistribution(data) {
        if (!data || !data.data) return;

        var distribution = data.data;
        var chartData = [];

        for (var i = 0; i < distribution.length; i++) {
            var item = distribution[i];
            chartData.push({
                name: item.name,
                value: item.value,
                itemStyle: {color: item.color}
            });
        }

        this.charts.businessPie.setOption({
            series: [{
                data: chartData
            }]
        });
    }

    updateBusinessTrend(data) {
        if (!data || !data.data) return;

        var history = data.data;
        var timestamps = history.timestamps || [];
        var series = history.series || [];

        var legendData = [];
        var seriesData = [];

        for (var i = 0; i < series.length; i++) {
            var item = series[i];
            legendData.push(item.name);
            seriesData.push({
                name: item.name,
                type: 'line',
                data: item.data,
                smooth: true,
                itemStyle: {color: item.color}
            });
        }

        this.charts.businessTrend.setOption({
            xAxis: {data: timestamps},
            legend: {data: legendData},
            series: seriesData
        });
    }

    updateSwitches(data) {
        if (!data || !data.data) return;

        var switches = data.data;
        var container = document.getElementById('switchesList');

        if (!container) return;

        if (!switches.length) {
            container.innerHTML = '<div class="text-muted text-center py-3">无交换机数据</div>';
            return;
        }

        var html = '';
        for (var i = 0; i < switches.length; i++) {
            var switchItem = switches[i];
            var statusClass = switchItem.state === 'connected' ? 'status-connected' : 'status-disconnected';
            var statusText = switchItem.state === 'connected' ? '已连接' : '断开';
            var portCount = (switchItem.ports && switchItem.ports.length) ? switchItem.ports.length : 0;

            html += '<div class="port-item mb-2">' +
                    '<div class="d-flex justify-content-between align-items-center">' +
                    '<div>' +
                    '<span class="' + statusClass + '"></span>' +
                    '<strong>' + switchItem.id + '</strong>' +
                    '<span class="badge bg-secondary ms-2">' + statusText + '</span>' +
                    '</div>' +
                    '<span class="badge bg-light text-dark">' + portCount + ' 端口</span>' +
                    '</div>' +
                    '</div>';
        }

        container.innerHTML = html;
    }

    updateAlerts(data) {
        if (!data || !data.data) return;

        var alerts = data.data;
        var container = document.getElementById('alertsList');
        var countElement = document.getElementById('alertCount');

        if (!container || !countElement) return;

        countElement.textContent = alerts.length;

        if (!alerts.length) {
            container.innerHTML = '<div class="text-muted text-center py-3">暂无告警</div>';
            return;
        }

        var html = '';
        for (var i = 0; i < alerts.length; i++) {
            var alert = alerts[i];
            var severityClass = 'alert-' + (alert.severity ? alert.severity.toLowerCase() : 'info');
            var timeStr = this.formatTimestamp(alert.timestamp);

            html += '<div class="alert-item ' + severityClass + '">' +
                    '<div class="d-flex justify-content-between">' +
                    '<strong>' + (alert.severity || 'UNKNOWN') + '</strong>' +
                    '<small class="text-muted">' + timeStr + '</small>' +
                    '</div>' +
                    '<div class="mt-1">' + (alert.message || '') + '</div>' +
                    '</div>';
        }

        container.innerHTML = html;
    }

    updateTopTalkers(data) {
        if (!data || !data.data) return;

        var topTalkers = data.data;
        var container = document.getElementById('topTalkersList');

        if (!container) return;

        if (!topTalkers.length) {
            container.innerHTML = '<div class="text-muted text-center py-3">无数据</div>';
            return;
        }

        // 找出最大流量值
        var maxTraffic = 0;
        for (var i = 0; i < topTalkers.length; i++) {
            if (topTalkers[i].total_bytes > maxTraffic) {
                maxTraffic = topTalkers[i].total_bytes;
            }
        }

        var html = '';
        for (var i = 0; i < topTalkers.length; i++) {
            var item = topTalkers[i];
            var percentage = maxTraffic > 0 ? (item.total_bytes / maxTraffic) * 100 : 0;
            var trafficStr = this.formatBytes(item.total_bytes);

            html += '<div class="top-talker-item">' +
                    '<div class="d-flex justify-content-between align-items-center">' +
                    '<div>' +
                    '<span class="badge bg-primary me-2">' + (i + 1) + '</span>' +
                    '<span>' + item.switch_id + ':' + item.port + '</span>' +
                    '</div>' +
                    '<strong>' + trafficStr + '</strong>' +
                    '</div>' +
                    '<div class="traffic-bar">' +
                    '<div class="traffic-bar-inner" style="width: ' + percentage + '%;"></div>' +
                    '</div>' +
                    '</div>';
        }

        container.innerHTML = html;
    }

    updatePortSelector(data) {
        if (!data || !data.data) return;

        var switches = data.data;
        var selector = document.getElementById('portSelector');

        if (!selector) return;

        var options = '<option value="">请选择端口...</option>';

        for (var i = 0; i < switches.length; i++) {
            var switchItem = switches[i];
            if (switchItem.ports && Array.isArray(switchItem.ports)) {
                for (var j = 0; j < switchItem.ports.length; j++) {
                    var port = switchItem.ports[j];
                    var value = switchItem.id + '_' + port.port_no;
                    var label = switchItem.id + ' - 端口 ' + port.port_no;
                    options += '<option value="' + value + '">' + label + '</option>';
                }
            }
        }

        selector.innerHTML = options;
    }

    updateHealthStatus(data) {
        if (!data || !data.data) return;

        var health = data.data;
        var container = document.getElementById('healthStatus');

        if (container) {
            container.textContent = health.data_source || health.service || '未知';
        }
    }

    loadPortHistory(portKey) {
        if (!portKey) return;

        var parts = portKey.split('_');
        var switchId = parts[0];
        var portNo = parts[1];

        this.fetchData('port-history/' + switchId + '/' + portNo)
            .then(function(data) {
                this.updatePortDetailChart(data.data);
            }.bind(this))
            .catch(function(error) {
                console.error('加载端口历史失败:', error);
                this.showToast('无法加载端口历史数据', 'warning');
            }.bind(this));
    }

    updatePortDetailChart(historyData) {
        if (!historyData) return;

        var timestamps = historyData.timestamps || [];
        var rxSpeeds = historyData.rx_speeds || [];
        var txSpeeds = historyData.tx_speeds || [];

        this.charts.portDetail.setOption({
            xAxis: {data: timestamps},
            series: [
                {name: '接收流量', data: rxSpeeds},
                {name: '发送流量', data: txSpeeds}
            ]
        });
    }

    updateLastUpdateTime() {
        var now = new Date();
        var timeStr = now.toLocaleTimeString('zh-CN');
        var element = document.getElementById('lastUpdateTime');
        if (element) {
            element.textContent = timeStr;
        }
    }

    refreshData() {
        console.log('手动刷新数据...');
        this.loadAllData();
    }

    startAutoRefresh() {
        this.stopAutoRefresh();
        var self = this;
        this.refreshTimer = setInterval(function() {
            self.refreshData();
        }, this.config.refreshInterval);
    }

    stopAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }

    showLoading() {
        var overlay = document.getElementById('loadingOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'loadingOverlay';
            overlay.className = 'loading-overlay';
            overlay.innerHTML = '<div class="text-center">' +
                                '<div class="loading-spinner mb-3"></div>' +
                                '<div class="text-white">加载数据中...</div>' +
                                '</div>';
            document.body.appendChild(overlay);
        }
        overlay.style.display = 'flex';
    }

    hideLoading() {
        var overlay = document.getElementById('loadingOverlay');
        if (overlay) {
            overlay.style.display = 'none';
        }
    }

    showToast(message, type) {
        if (!type) type = 'info';

        var typeConfig = {
            info: { icon: 'info-circle', color: 'primary' },
            success: { icon: 'check-circle', color: 'success' },
            warning: { icon: 'exclamation-triangle', color: 'warning' },
            danger: { icon: 'times-circle', color: 'danger' }
        };

        var config = typeConfig[type] || typeConfig.info;
        var toastId = 'toast-' + Date.now();

        var toastHTML = '<div id="' + toastId + '" class="position-fixed top-0 end-0 p-3" style="z-index: 1056">' +
                        '<div class="toast align-items-center text-white bg-' + config.color + ' border-0" role="alert">' +
                        '<div class="d-flex">' +
                        '<div class="toast-body d-flex align-items-center">' +
                        '<i class="fas fa-' + config.icon + ' me-2"></i>' +
                        message +
                        '</div>' +
                        '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>' +
                        '</div>' +
                        '</div>' +
                        '</div>';

        document.body.insertAdjacentHTML('beforeend', toastHTML);

        var toastElement = document.getElementById(toastId);
        var toast = new bootstrap.Toast(toastElement.querySelector('.toast'), {
            delay: 3000
        });
        toast.show();

        var selfToastElement = toastElement;
        toastElement.addEventListener('hidden.bs.toast', function() {
            if (selfToastElement && selfToastElement.parentNode) {
                selfToastElement.parentNode.removeChild(selfToastElement);
            }
        });
    }

    // 工具函数
    formatBytes(bytes) {
        if (bytes === 0 || bytes === undefined) return '0 B';
        var k = 1024;
        var sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        var i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    formatSpeed(bps) {
        if (bps === 0 || bps === undefined) return '0 bps';
        if (bps < 1000) return bps.toFixed(2) + ' bps';
        if (bps < 1000000) return (bps / 1000).toFixed(2) + ' Kbps';
        if (bps < 1000000000) return (bps / 1000000).toFixed(2) + ' Mbps';
        return (bps / 1000000000).toFixed(2) + ' Gbps';
    }

    formatTimestamp(timestamp) {
        if (!timestamp) return '--:--:--';
        var date = new Date(timestamp * 1000);
        return date.toLocaleTimeString('zh-CN');
    }
}

// 当文档加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM加载完成，初始化仪表板...');
    window.dashboard = new SDNDashboard();
});