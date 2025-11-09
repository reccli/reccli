import React, { memo } from 'react';
import { Box, Text } from 'ink';
import Spinner from 'ink-spinner';
// Memoized message component to prevent re-renders
const Message = memo(({ message }) => {
    const color = message.role === 'user' ? 'cyan' : message.role === 'system' ? 'yellow' : undefined;
    // For very large content, chunk it to avoid Text component limits
    // Chunk by ~2000 chars to stay well under any Ink limits
    const CHUNK_SIZE = 2000;
    const chunks = [];
    if (message.content.length > CHUNK_SIZE) {
        for (let i = 0; i < message.content.length; i += CHUNK_SIZE) {
            chunks.push(message.content.slice(i, i + CHUNK_SIZE));
        }
    }
    else {
        chunks.push(message.content);
    }
    return (React.createElement(Box, { flexDirection: "column", marginBottom: 1 },
        message.annotation && (React.createElement(Box, null,
            React.createElement(Text, { color: "gray", backgroundColor: "darkGray" }, message.annotation))),
        React.createElement(Text, { color: "gray" }, '─'.repeat(60)),
        React.createElement(Box, { paddingLeft: 2, flexDirection: "column" }, chunks.map((chunk, i) => (React.createElement(Text, { key: i, color: color }, chunk)))),
        React.createElement(Text, { color: "gray" }, '─'.repeat(60))));
});
export const MessageList = memo(({ messages, isLoading }) => {
    // Debug: Log message content before rendering
    messages.forEach((msg, i) => {
        console.error(`[MessageList] Message ${i}: ${msg.content.length} chars, ${msg.content.split('\n').length} lines, role: ${msg.role}`);
    });
    return (React.createElement(Box, { flexDirection: "column" },
        messages.map((message, index) => (React.createElement(Message, { key: index, message: message }))),
        isLoading && (React.createElement(Box, null,
            React.createElement(Text, { color: "blue" },
                React.createElement(Spinner, { type: "dots" })),
            React.createElement(Text, { color: "blue" }, " Thinking...")))));
});
//# sourceMappingURL=MessageList.js.map