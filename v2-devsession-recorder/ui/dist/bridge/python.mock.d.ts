/**
 * Mock Python bridge for testing the UI without the backend
 */
export declare class PythonBridge {
    private static instance;
    private tokenCount;
    private constructor();
    static getInstance(): PythonBridge;
    initialize(): Promise<void>;
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
//# sourceMappingURL=python.mock.d.ts.map