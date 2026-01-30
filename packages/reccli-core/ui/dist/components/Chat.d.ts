import React from 'react';
export interface ToolCall {
    name: string;
    input: any;
    result?: string;
}
export interface StreamingMessage {
    textChunks: string[];
    toolCalls: ToolCall[];
}
export declare const ChatApp: React.FC;
//# sourceMappingURL=Chat.d.ts.map