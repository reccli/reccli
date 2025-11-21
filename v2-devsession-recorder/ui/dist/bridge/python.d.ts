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
export declare class PythonBridge {
    private static instance;
    private pythonProcess;
    private messageQueue;
    private streamHandlers;
    private messageId;
    private buffer;
    private constructor();
    static getInstance(): PythonBridge;
    initialize(): Promise<void>;
    private processBuffer;
    private waitForReady;
    private sendRaw;
    sendMessage(content: string): Promise<{
        content: string;
        tokenCount: number;
    }>;
    getSessionInfo(): Promise<{
        name: string;
        tokenCount: number;
        maxTokens: number;
    }>;
    sendMessageStreaming(content: string, onEvent: (event: StreamEvent) => void): Promise<void>;
    close(): void;
}
export type { StreamEvent };
//# sourceMappingURL=python.d.ts.map