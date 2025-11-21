import {spawn, ChildProcess} from 'child_process';
import * as path from 'path';
import {fileURLToPath} from 'url';
import {dirname} from 'path';

interface Message {
  id: string;
  method: string;
  params: any;
}

interface Response {
  id: string;
  result?: any;
  error?: string;
}

interface StreamEvent {
  id: string;
  type: 'text_chunk' | 'tool_call_start' | 'tool_call_result' | 'final_response' | 'error';
  content?: string;
  tool_name?: string;
  tool_input?: any;
  result?: string;
  message?: string;
  complete?: boolean;
}

interface StreamHandler {
  onEvent: (event: StreamEvent) => void;
  resolve: Function;
  reject: Function;
}

export class PythonBridge {
  private static instance: PythonBridge;
  private pythonProcess: ChildProcess | null = null;
  private messageQueue: Map<string, {resolve: Function, reject: Function}> = new Map();
  private streamHandlers: Map<string, StreamHandler> = new Map();
  private messageId = 0;
  private buffer = '';

  private constructor() {}

  static getInstance(): PythonBridge {
    if (!PythonBridge.instance) {
      PythonBridge.instance = new PythonBridge();
    }
    return PythonBridge.instance;
  }

  async initialize(): Promise<void> {
    if (this.pythonProcess) {
      return;
    }

    // Start the Python backend server
    const __filename = fileURLToPath(import.meta.url);
    const __dirname = dirname(__filename);
    const backendPath = path.join(__dirname, '..', '..', '..', 'backend', 'server.py');
    this.pythonProcess = spawn('python3', [backendPath], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: {...process.env}
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

  private processBuffer() {
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.trim()) {
        try {
          const data = JSON.parse(line);

          // Check if it's a streaming event
          if (data.type) {
            const event = data as StreamEvent;
            const handler = this.streamHandlers.get(event.id);

            if (handler) {
              if (event.type === 'final_response') {
                handler.resolve();
                this.streamHandlers.delete(event.id);
              } else if (event.type === 'error') {
                handler.reject(new Error(event.message));
                this.streamHandlers.delete(event.id);
              } else {
                handler.onEvent(event);
              }
            }
          } else {
            // Regular non-streaming response
            const response = data as Response;
            const promise = this.messageQueue.get(response.id);
            if (promise) {
              this.messageQueue.delete(response.id);
              if (response.error) {
                promise.reject(new Error(response.error));
              } else {
                promise.resolve(response.result);
              }
            }
          }
        } catch (e) {
          console.error('Failed to parse response:', line, e);
        }
      }
    }
  }

  private async waitForReady(): Promise<void> {
    return new Promise((resolve) => {
      const checkReady = () => {
        this.sendRaw({id: 'ready', method: 'ping', params: {}}).then(resolve).catch(() => {
          setTimeout(checkReady, 100);
        });
      };
      checkReady();
    });
  }

  private async sendRaw(message: Message): Promise<any> {
    return new Promise((resolve, reject) => {
      if (!this.pythonProcess || !this.pythonProcess.stdin) {
        reject(new Error('Python process not initialized'));
        return;
      }

      this.messageQueue.set(message.id, {resolve, reject});
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

  async sendMessage(content: string): Promise<{content: string, tokenCount: number}> {
    const id = `msg_${++this.messageId}`;
    return this.sendRaw({
      id,
      method: 'chat',
      params: {content}
    });
  }

  async getSessionInfo(): Promise<{name: string, tokenCount: number, maxTokens: number}> {
    const id = `info_${++this.messageId}`;
    return this.sendRaw({
      id,
      method: 'getSessionInfo',
      params: {}
    });
  }

  async sendMessageStreaming(
    content: string,
    onEvent: (event: StreamEvent) => void
  ): Promise<void> {
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
        params: {content}
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

// Export StreamEvent type for use in other components
export type {StreamEvent};