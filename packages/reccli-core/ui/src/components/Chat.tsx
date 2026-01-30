import React, {useState, useEffect, useRef} from 'react';
import {Box, Text, useApp} from 'ink';
import {SmartInput} from './InputV3.js';  // Use V3 with raw stdin handling
import {MessageList} from './MessageList.js';
import {StatusBar} from './Status.js';
// Use real bridge for production
import {PythonBridge, StreamEvent} from '../bridge/python.js';

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  annotation?: string; // For paste annotations
}

export interface ToolCall {
  name: string;
  input: any;
  result?: string;
}

export interface StreamingMessage {
  textChunks: string[];
  toolCalls: ToolCall[];
}

export const ChatApp: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [currentStreamContent, setCurrentStreamContent] = useState<StreamingMessage | null>(null);
  const [sessionInfo, setSessionInfo] = useState({
    name: 'session_' + new Date().toISOString().slice(0, 19).replace(/:/g, ''),
    tokenCount: 0,
    maxTokens: 150000
  });
  const abortControllerRef = useRef<AbortController | null>(null);

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

  const handleCancel = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setIsLoading(false);
      setMessages(prev => [...prev, {
        role: 'system',
        content: 'Request cancelled by user'
      }]);
    }
  };

  const handleInput = async (text: string, annotation?: string) => {
    console.error(`[Chat.tsx] handleInput called: ${text.length} chars`);
    console.error(`[Chat.tsx] Annotation: ${annotation}`);

    // Handle special commands
    if (text.toLowerCase() === 'exit' || text.toLowerCase() === 'quit') {
      exit();
      return;
    }

    // Add user message with optional annotation
    const userMessage: Message = {
      role: 'user',
      content: text,
      annotation
    };
    setMessages(prev => [...prev, userMessage]);
    setIsLoading(true);

    // Initialize streaming message
    const streamMsg: StreamingMessage = {
      textChunks: [],
      toolCalls: []
    };
    setCurrentStreamContent(streamMsg);

    // Create abort controller for this request
    abortControllerRef.current = new AbortController();

    try {
      // Send with streaming
      await bridge.sendMessageStreaming(text, (event: StreamEvent) => {
        if (event.type === 'text_chunk' && event.content) {
          streamMsg.textChunks.push(event.content);
          setCurrentStreamContent({...streamMsg});
        } else if (event.type === 'tool_call_start' && event.tool_name) {
          streamMsg.toolCalls.push({
            name: event.tool_name,
            input: event.tool_input
          });
          setCurrentStreamContent({...streamMsg});
        } else if (event.type === 'tool_call_result' && event.tool_name) {
          const lastCall = streamMsg.toolCalls[streamMsg.toolCalls.length - 1];
          if (lastCall && lastCall.name === event.tool_name) {
            lastCall.result = event.result;
            setCurrentStreamContent({...streamMsg});
          }
        }
      });

      // Finalize: convert streaming message to regular message
      const fullContent = [
        ...streamMsg.textChunks,
        ...streamMsg.toolCalls.map(call =>
          `\n[Tool: ${call.name}]\nInput: ${JSON.stringify(call.input, null, 2)}\nResult: ${call.result || '(pending)'}`
        )
      ].join('\n');

      setMessages(prev => [...prev, {
        role: 'assistant',
        content: fullContent
      }]);
      setCurrentStreamContent(null);

    } catch (error) {
      // Don't show error if it was an abort
      if (error instanceof Error && error.name === 'AbortError') {
        return;
      }
      setMessages(prev => [...prev, {
        role: 'system',
        content: `Error: ${error instanceof Error ? error.message : String(error)}`
      }]);
      setCurrentStreamContent(null);
    } finally {
      abortControllerRef.current = null;
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

      {/* Messages - use terminal's native scrollback */}
      <Box flexDirection="column" paddingX={1}>
        <MessageList messages={messages} isLoading={isLoading} streamingContent={currentStreamContent} />
      </Box>

      {/* Status bar - fixed height */}
      <Box flexShrink={0}>
        <StatusBar {...sessionInfo} />
      </Box>

      {/* Input - fixed height */}
      <Box flexShrink={0} paddingX={1}>
        <SmartInput onSubmit={handleInput} onCancel={handleCancel} isDisabled={isLoading} />
      </Box>
    </Box>
  );
};