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
// Streaming message component
const StreamingMessageComponent = memo(({ content }) => {
    return (React.createElement(Box, { flexDirection: "column" },
        React.createElement(Text, { color: "gray" }, '─'.repeat(60)),
        React.createElement(Box, { paddingLeft: 2, flexDirection: "column" }, content.textChunks.map((chunk, i) => (React.createElement(Text, { key: `text-${i}` }, chunk)))),
        content.toolCalls.map((call, i) => (React.createElement(Box, { key: `tool-${i}`, flexDirection: "column", marginY: 1, paddingLeft: 2 },
            React.createElement(Text, { color: "cyan" },
                "[Tool Use: ",
                call.name,
                "]"),
            React.createElement(Text, { color: "gray" }, JSON.stringify(call.input, null, 2)),
            call.result && (React.createElement(React.Fragment, null,
                React.createElement(Text, { color: "cyan" }, "[Tool Result]"),
                React.createElement(Text, { color: "gray" }, call.result))),
            !call.result && (React.createElement(Text, { color: "yellow" },
                React.createElement(Spinner, { type: "dots" }),
                " Executing..."))))),
        React.createElement(Text, { color: "gray" }, '─'.repeat(60))));
});
export const MessageList = memo(({ messages, isLoading, streamingContent }) => {
    return (React.createElement(Box, { flexDirection: "column" },
        messages.map((message, index) => (React.createElement(Message, { key: index, message: message }))),
        streamingContent && (React.createElement(StreamingMessageComponent, { content: streamingContent })),
        isLoading && !streamingContent && (React.createElement(Box, null,
            React.createElement(Text, { color: "blue" },
                React.createElement(Spinner, { type: "dots" })),
            React.createElement(Text, { color: "blue" }, " Thinking...")))));
});
//# sourceMappingURL=MessageList.js.map