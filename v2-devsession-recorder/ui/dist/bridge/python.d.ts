export declare class PythonBridge {
    private static instance;
    private pythonProcess;
    private messageQueue;
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
    close(): void;
}
//# sourceMappingURL=python.d.ts.map