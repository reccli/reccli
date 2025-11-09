import React, {useState, useEffect} from 'react';
import {Box, Text, useApp} from 'ink';
import {SmartInput} from './InputV3.js';  // Use V3 with raw stdin handling
import {MessageList} from './MessageList.js';
import {StatusBar} from './Status.js';
// Use real bridge for production
import {PythonBridge} from '../bridge/python.js';

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  annotation?: string; // For paste annotations
}

export const ChatApp: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionInfo, setSessionInfo] = useState({
    name: 'session_' + new Date().toISOString().slice(0, 19).replace(/:/g, ''),
    tokenCount: 0,
    maxTokens: 150000
  });

  const {exit} = useApp();
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

  const handleInput = async (text: string, annotation?: string) => {
    // Handle special commands
    if (text.toLowerCase() === 'exit' || text.toLowerCase() === 'quit') {
      exit();
      return;
    }

    // For large messages, print directly to stdout instead of using Ink
    // This bypasses Ink's rendering engine which corrupts large text blocks
    const isLargeMessage = text.length > 1000;

    if (isLargeMessage) {
      // Print user message directly to stdout
      process.stdout.write('\n');
      if (annotation) {
        process.stdout.write(`\x1b[90m${annotation}\x1b[0m\n`); // gray
      }
      process.stdout.write('─'.repeat(60) + '\n');
      process.stdout.write(`\x1b[36m${text}\x1b[0m\n`); // cyan for user
      process.stdout.write('─'.repeat(60) + '\n');
    }

    // Add user message with optional annotation
    const userMessage: Message = {
      role: 'user',
      content: text,
      annotation
    };
    // Debug: log message content length and preview
    console.error(`[Chat.tsx] Adding user message: ${text.length} chars, ${text.split('\n').length} lines`);
    console.error(`[Chat.tsx] First 100 chars: ${text.substring(0, 100)}`);
    console.error(`[Chat.tsx] Last 100 chars: ${text.substring(text.length - 100)}`);

    // Only add to messages state if NOT large (to avoid Ink rendering it)
    if (!isLargeMessage) {
      setMessages(prev => [...prev, userMessage]);
    }
    setIsLoading(true);

    try {
      // Send to Python backend
      const response = await bridge.sendMessage(text);

      // For large responses, print directly to stdout
      const isLargeResponse = response.content.length > 1000;

      if (isLargeResponse) {
        process.stdout.write('\n─'.repeat(60) + '\n');
        process.stdout.write(response.content + '\n');
        process.stdout.write('─'.repeat(60) + '\n');
      }

      // Add assistant response (only to state if not large)
      if (!isLargeResponse) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: response.content
        }]);
      }

      // Update session info
      setSessionInfo(prev => ({
        ...prev,
        tokenCount: response.tokenCount
      }));
    } catch (error) {
      setMessages(prev => [...prev, {
        role: 'system',
        content: `Error: ${error instanceof Error ? error.message : String(error)}`
      }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Box flexDirection="column">
      {/* Header - fixed height */}
      <Box flexShrink={0} borderStyle="double" borderColor="cyan" paddingX={1}>
        <Text color="red">● </Text>
        <Text bold>RecCli Chat</Text>
      </Box>

      {/* Messages - scrollable container, doesn't recalculate on input changes */}
      <Box flexGrow={1} flexShrink={1} flexDirection="column" paddingX={1} overflow="hidden">
        <MessageList messages={messages} isLoading={isLoading} />
      </Box>

      {/* Status bar - fixed height */}
      <Box flexShrink={0}>
        <StatusBar {...sessionInfo} />
      </Box>

      {/* Input - fixed height */}
      <Box flexShrink={0} paddingX={1}>
        <SmartInput onSubmit={handleInput} isDisabled={isLoading} />
      </Box>
    </Box>
  );
};