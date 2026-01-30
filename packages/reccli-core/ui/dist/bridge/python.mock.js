/**
 * Mock Python bridge for testing the UI without the backend
 */
export class PythonBridge {
    static instance;
    tokenCount = 0;
    constructor() { }
    static getInstance() {
        if (!PythonBridge.instance) {
            PythonBridge.instance = new PythonBridge();
        }
        return PythonBridge.instance;
    }
    async initialize() {
        // Mock initialization
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    async sendMessage(content) {
        // Simulate API delay
        await new Promise(resolve => setTimeout(resolve, 1500));
        // Update token count
        this.tokenCount += Math.floor(content.length * 1.5 + 200);
        // Mock response based on input
        let response = '';
        if (content.toLowerCase().includes('hello')) {
            response = 'Hello! How can I help you today?';
        }
        else if (content.toLowerCase().includes('test')) {
            response = 'This is a test response from the mock backend. The paste detection appears to be working correctly!';
        }
        else {
            response = `I received your message with ${content.length} characters. The paste detection system is working well - large pastes are being properly annotated as [pasted +X lines].`;
        }
        return {
            content: response,
            tokenCount: this.tokenCount
        };
    }
    async getSessionInfo() {
        return {
            name: 'mock_session_' + new Date().toISOString().slice(0, 19).replace(/:/g, ''),
            tokenCount: this.tokenCount,
            maxTokens: 150000
        };
    }
    close() {
        // Mock cleanup
    }
}
//# sourceMappingURL=python.mock.js.map