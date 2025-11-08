// Q-Sensor Live Data Dashboard

const API_BASE = window.location.origin;
const WS_URL = `ws://${window.location.host}/stream`;

// State
let ws = null;
let chart = null;
let dataPoints = [];
let maxDataPoints = 60; // Show last 60 seconds
let dataCount = 0;
let lastTimestamp = null;
let sampleRate = 0;

// UI Elements
const elements = {
    statusBadge: document.getElementById('status-badge'),
    statusText: document.getElementById('status-text'),
    currentValue: document.getElementById('current-value'),
    sensorId: document.getElementById('sensor-id'),
    mode: document.getElementById('mode'),
    sampleRate: document.getElementById('sample-rate'),
    dataPoints: document.getElementById('data-points'),
    btnConnect: document.getElementById('btn-connect'),
    btnStart: document.getElementById('btn-start'),
    btnStop: document.getElementById('btn-stop'),
    btnDisconnect: document.getElementById('btn-disconnect')
};

// Initialize Chart
function initChart() {
    const ctx = document.getElementById('data-chart').getContext('2d');
    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Sensor Value',
                data: [],
                borderColor: '#00bcd4',
                backgroundColor: 'rgba(0, 188, 212, 0.1)',
                borderWidth: 2,
                tension: 0.4,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {
                x: {
                    display: true,
                    grid: {
                        color: 'rgba(255, 255, 255, 0.1)'
                    },
                    ticks: {
                        color: '#ccc',
                        maxTicksLimit: 10
                    }
                },
                y: {
                    display: true,
                    grid: {
                        color: 'rgba(255, 255, 255, 0.1)'
                    },
                    ticks: {
                        color: '#ccc'
                    }
                }
            },
            plugins: {
                legend: {
                    display: false
                }
            }
        }
    });
}

// WebSocket Connection
function connectWebSocket() {
    console.log('Connecting to WebSocket:', WS_URL);

    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log('WebSocket connected');
        updateStatus('streaming', 'Streaming');
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateData(data);
        } catch (e) {
            console.error('Error parsing WebSocket data:', e);
        }
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        updateStatus('error', 'Error');
    };

    ws.onclose = () => {
        console.log('WebSocket closed');
        ws = null;
        if (elements.statusText.textContent === 'Streaming') {
            updateStatus('connected', 'Connected');
        }
    };
}

function disconnectWebSocket() {
    if (ws) {
        ws.close();
        ws = null;
    }
}

// Update Data
function updateData(data) {
    // Update current value
    const value = data.data?.value ?? data.value ?? '--';
    elements.currentValue.textContent = typeof value === 'number' ? value.toFixed(4) : value;

    // Update sensor info
    if (data.sensor_id) {
        elements.sensorId.textContent = data.sensor_id;
    }
    if (data.mode) {
        elements.mode.textContent = data.mode;
    }

    // Calculate sample rate
    const now = Date.now();
    if (lastTimestamp) {
        const deltaMs = now - lastTimestamp;
        if (deltaMs > 0) {
            sampleRate = (sampleRate * 0.9) + (1000 / deltaMs * 0.1); // Smooth average
            elements.sampleRate.textContent = sampleRate.toFixed(1) + ' Hz';
        }
    }
    lastTimestamp = now;

    // Update data count
    dataCount++;
    elements.dataPoints.textContent = dataCount.toLocaleString();

    // Update chart
    if (typeof value === 'number') {
        const timestamp = new Date(data.ts || now).toLocaleTimeString();

        dataPoints.push({ x: timestamp, y: value });

        // Keep only last N points
        if (dataPoints.length > maxDataPoints) {
            dataPoints.shift();
        }

        chart.data.labels = dataPoints.map(p => p.x);
        chart.data.datasets[0].data = dataPoints.map(p => p.y);
        chart.update('none'); // No animation for performance
    }
}

// Status Updates
function updateStatus(state, text) {
    elements.statusBadge.className = 'status-badge status-' + state;
    elements.statusText.textContent = text;

    // Update button states
    const isDisconnected = state === 'disconnected';
    const isConnected = state === 'connected' || state === 'streaming';
    const isStreaming = state === 'streaming';

    elements.btnConnect.disabled = !isDisconnected;
    elements.btnStart.disabled = !isConnected || isStreaming;
    elements.btnStop.disabled = !isStreaming;
    elements.btnDisconnect.disabled = isDisconnected;
}

// API Calls
async function apiCall(endpoint, method = 'POST', params = {}) {
    try {
        let url = `${API_BASE}${endpoint}`;
        if (method === 'GET' && Object.keys(params).length > 0) {
            url += '?' + new URLSearchParams(params).toString();
        }

        const options = {
            method,
            headers: { 'Content-Type': 'application/json' }
        };

        if (method === 'POST' && Object.keys(params).length > 0) {
            options.body = JSON.stringify(params);
        }

        const response = await fetch(url, options);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || `HTTP ${response.status}`);
        }

        return data;
    } catch (error) {
        console.error(`API call failed (${endpoint}):`, error);
        alert(`Error: ${error.message}`);
        throw error;
    }
}

// Button Handlers
elements.btnConnect.addEventListener('click', async () => {
    updateStatus('connecting', 'Connecting...');
    try {
        await apiCall('/connect');
        updateStatus('connected', 'Connected');
        console.log('Sensor connected successfully');
    } catch (error) {
        updateStatus('disconnected', 'Disconnected');
    }
});

elements.btnStart.addEventListener('click', async () => {
    try {
        await apiCall('/start');
        connectWebSocket();
        console.log('Acquisition started');
    } catch (error) {
        updateStatus('connected', 'Connected');
    }
});

elements.btnStop.addEventListener('click', async () => {
    try {
        await apiCall('/stop');
        disconnectWebSocket();
        updateStatus('connected', 'Connected');
        console.log('Acquisition stopped');
    } catch (error) {
        // Still disconnect WebSocket even if API call fails
        disconnectWebSocket();
        updateStatus('connected', 'Connected');
    }
});

elements.btnDisconnect.addEventListener('click', async () => {
    disconnectWebSocket();
    updateStatus('disconnecting', 'Disconnecting...');
    try {
        await apiCall('/disconnect');
        updateStatus('disconnected', 'Disconnected');

        // Reset display
        elements.currentValue.textContent = '--';
        elements.sensorId.textContent = '--';
        elements.mode.textContent = '--';
        elements.sampleRate.textContent = '-- Hz';
        dataPoints = [];
        dataCount = 0;
        lastTimestamp = null;
        sampleRate = 0;
        chart.data.labels = [];
        chart.data.datasets[0].data = [];
        chart.update();

        console.log('Sensor disconnected');
    } catch (error) {
        updateStatus('disconnected', 'Disconnected');
    }
});

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    console.log('Q-Sensor Dashboard initialized');
    initChart();
    updateStatus('disconnected', 'Disconnected');

    // Check initial status
    fetch(`${API_BASE}/status`)
        .then(r => r.json())
        .then(data => {
            console.log('Initial status:', data);
            if (data.connected) {
                updateStatus('connected', 'Connected');
                if (data.sensor_id) {
                    elements.sensorId.textContent = data.sensor_id;
                }
                if (data.mode) {
                    elements.mode.textContent = data.mode;
                }
            }
        })
        .catch(e => console.error('Failed to fetch initial status:', e));
});
