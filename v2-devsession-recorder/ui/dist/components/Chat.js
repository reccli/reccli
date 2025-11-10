import React, { useState, useEffect } from 'react';
import { Box, Text, useApp } from 'ink';
import { SmartInput } from './InputV3.js'; // Use V3 with raw stdin handling
import { MessageList } from './MessageList.js';
import { StatusBar } from './Status.js';
// Use real bridge for production
import { PythonBridge } from '../bridge/python.js';
export const ChatApp = () => {
    const [messages, setMessages] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [sessionInfo, setSessionInfo] = useState({
        name: 'session_' + new Date().toISOString().slice(0, 19).replace(/:/g, ''),
        tokenCount: 0,
        maxTokens: 150000
    });
    const { exit } = useApp();
    const bridge = PythonBridge.getInstance();
    // Initialize bridge on mount
    useEffect(() => {
        bridge.initialize().catch(error => {
            setMessages([{
                    role: 'system',
                    content: `Failed to initialize backend: ${error instanceof Error ? error.message : String(error)}`
                }]);
        });
        return () => {
            bridge.close();
        };
    }, []);
    const handleInput = async (text, annotation) => {
        // Handle special commands
        if (text.toLowerCase() === 'exit' || text.toLowerCase() === 'quit') {
            exit();
            return;
        }
        // Add user message with optional annotation
        const userMessage = {
            role: 'user',
            content: text,
            annotation
        };
        setMessages(prev => [...prev, userMessage]);
        setIsLoading(true);
        try {
            // Send to Python backend
            const response = await bridge.sendMessage(text);
            // Add assistant response
            setMessages(prev => [...prev, {
                    role: 'assistant',
                    content: response.content
                }]);
            // Update session info
            setSessionInfo(prev => ({
                ...prev,
                tokenCount: response.tokenCount
            }));
        }
        catch (error) {
            setMessages(prev => [...prev, {
                    role: 'system',
                    content: `Error: ${error instanceof Error ? error.message : String(error)}`
                }]);
        }
        finally {
            setIsLoading(false);
        }
    };
    return (React.createElement(Box, { flexDirection: "column" },
        React.createElement(Box, { flexShrink: 0, borderStyle: "double", borderColor: "cyan", paddingX: 1 },
            React.createElement(Text, { color: "red" }, "\u25CF "),
            React.createElement(Text, { bold: true }, "RecCli Chat")),
        React.createElement(Box, { flexDirection: "column", paddingX: 1 },
            React.createElement(MessageList, { messages: messages, isLoading: isLoading })),
        React.createElement(Box, { flexShrink: 0 },
            React.createElement(StatusBar, { ...sessionInfo })),
        React.createElement(Box, { flexShrink: 0, paddingX: 1 },
            React.createElement(SmartInput, { onSubmit: handleInput, isDisabled: isLoading }))));
};
//# sourceMappingURL=Chat.js.map