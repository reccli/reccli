import { spawn } from 'child_process';
import * as path from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';
export class PythonBridge {
    static instance;
    pythonProcess = null;
    messageQueue = new Map();
    streamHandlers = new Map();
    messageId = 0;
    buffer = '';
    constructor() { }
    static getInstance() {
        if (!PythonBridge.instance) {
            PythonBridge.instance = new PythonBridge();
        }
        return PythonBridge.instance;
    }
    async initialize() {
        if (this.pythonProcess) {
            return;
        }
        // Start the Python backend server
        const __filename = fileURLToPath(import.meta.url);
        const __dirname = dirname(__filename);
        const backendPath = path.join(__dirname, '..', '..', '..', 'backend', 'server.py');
        this.pythonProcess = spawn('python3', [backendPath], {
            stdio: ['pipe', 'pipe', 'pipe'],
            env: { ...process.env }
        });
        // Handle stdout from Python (JSON-RPC responses)
        this.pythonProcess.stdout?.on('data', (data) => {
            this.buffer += data.toString();
            this.processBuffer();
        });
        // Handle stderr (errors/logging)
        this.pythonProcess.stderr?.on('data', (data) => {
            console.error('Python stderr:', data.toString());
        });
        // Handle process exit
        this.pythonProcess.on('exit', (code) => {
            console.error(`Python process exited with code ${code}`);
            this.pythonProcess = null;
        });
        // Wait for ready signal
        await this.waitForReady();
    }
    processBuffer() {
        const lines = this.buffer.split('\n');
        this.buffer = lines.pop() || '';
        for (const line of lines) {
            if (line.trim()) {
                try {
                    const data = JSON.parse(line);
                    // Check if it's a streaming event
                    if (data.type) {
                        const event = data;
                        const handler = this.streamHandlers.get(event.id);
                        if (handler) {
                            if (event.type === 'final_response') {
                                handler.resolve();
                                this.streamHandlers.delete(event.id);
                            }
                            else if (event.type === 'error') {
                                handler.reject(new Error(event.message));
                                this.streamHandlers.delete(event.id);
                            }
                            else {
                                handler.onEvent(event);
                            }
                        }
                    }
                    else {
                        // Regular non-streaming response
                        const response = data;
                        const promise = this.messageQueue.get(response.id);
                        if (promise) {
                            this.messageQueue.delete(response.id);
                            if (response.error) {
                                promise.reject(new Error(response.error));
                            }
                            else {
                                promise.resolve(response.result);
                            }
                        }
                    }
                }
                catch (e) {
                    console.error('Failed to parse response:', line, e);
                }
            }
        }
    }
    async waitForReady() {
        return new Promise((resolve) => {
            const checkReady = () => {
                this.sendRaw({ id: 'ready', method: 'ping', params: {} }).then(resolve).catch(() => {
                    setTimeout(checkReady, 100);
                });
            };
            checkReady();
        });
    }
    async sendRaw(message) {
        return new Promise((resolve, reject) => {
            if (!this.pythonProcess || !this.pythonProcess.stdin) {
                reject(new Error('Python process not initialized'));
                return;
            }
            this.messageQueue.set(message.id, { resolve, reject });
            this.pythonProcess.stdin.write(JSON.stringify(message) + '\n');
            // Timeout after 30 seconds
            setTimeout(() => {
                if (this.messageQueue.has(message.id)) {
                    this.messageQueue.delete(message.id);
                    reject(new Error('Request timeout'));
                }
            }, 30000);
        });
    }
    async sendMessage(content) {
        const id = `msg_${++this.messageId}`;
        return this.sendRaw({
            id,
            method: 'chat',
            params: { content }
        });
    }
    async getSessionInfo() {
        const id = `info_${++this.messageId}`;
        return this.sendRaw({
            id,
            method: 'getSessionInfo',
            params: {}
        });
    }
    async sendMessageStreaming(content, onEvent) {
        const id = `msg_${++this.messageId}`;
        return new Promise((resolve, reject) => {
            if (!this.pythonProcess || !this.pythonProcess.stdin) {
                reject(new Error('Python process not initialized'));
                return;
            }
            // Set up streaming handler
            this.streamHandlers.set(id, {
                onEvent,
                resolve,
                reject
            });
            // Send request
            this.pythonProcess.stdin.write(JSON.stringify({
                id,
                method: 'chat_streaming',
                params: { content }
            }) + '\n');
            // Timeout after 60 seconds
            setTimeout(() => {
                if (this.streamHandlers.has(id)) {
                    this.streamHandlers.delete(id);
                    reject(new Error('Request timeout'));
                }
            }, 60000);
        });
    }
    close() {
        if (this.pythonProcess) {
            this.pythonProcess.kill();
            this.pythonProcess = null;
        }
    }
}
//# sourceMappingURL=python.js.map