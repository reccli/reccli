import React, {memo} from 'react';
import {Box, Text} from 'ink';
import Spinner from 'ink-spinner';
import {StreamingMessage, ToolCall} from './Chat.js';

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  annotation?: string;
}

interface MessageListProps {
  messages: Message[];
  isLoading: boolean;
  streamingContent?: StreamingMessage | null;
}

// Memoized message component to prevent re-renders
const Message = memo(({message}: {message: Message}) => {
  const color = message.role === 'user' ? 'cyan' : message.role === 'system' ? 'yellow' : undefined;

  // For very large content, chunk it to avoid Text component limits
  // Chunk by ~2000 chars to stay well under any Ink limits
  const CHUNK_SIZE = 2000;
  const chunks: string[] = [];

  if (message.content.length > CHUNK_SIZE) {
    for (let i = 0; i < message.content.length; i += CHUNK_SIZE) {
      chunks.push(message.content.slice(i, i + CHUNK_SIZE));
    }
  } else {
    chunks.push(message.content);
  }

  return (
    <Box flexDirection="column" marginBottom={1}>
      {/* Show annotation if it's a paste */}
      {message.annotation && (
        <Box>
          <Text color="gray" backgroundColor="darkGray">
            {message.annotation}
          </Text>
        </Box>
      )}

      {/* Separator line */}
      <Text color="gray">{'─'.repeat(60)}</Text>

      {/* Message content - chunked for large messages */}
      <Box paddingLeft={2} flexDirection="column">
        {chunks.map((chunk, i) => (
          <Text key={i} color={color}>{chunk}</Text>
        ))}
      </Box>

      {/* Bottom separator */}
      <Text color="gray">{'─'.repeat(60)}</Text>
    </Box>
  );
});

// Streaming message component
const StreamingMessageComponent = memo(({content}: {content: StreamingMessage}) => {
  return (
    <Box flexDirection="column">
      {/* Show separator */}
      <Text color="gray">{'─'.repeat(60)}</Text>

      {/* Show text chunks */}
      <Box paddingLeft={2} flexDirection="column">
        {content.textChunks.map((chunk, i) => (
          <Text key={`text-${i}`}>{chunk}</Text>
        ))}
      </Box>

      {/* Show tool calls */}
      {content.toolCalls.map((call, i) => (
        <Box key={`tool-${i}`} flexDirection="column" marginY={1} paddingLeft={2}>
          <Text color="cyan">[Tool Use: {call.name}]</Text>
          <Text color="gray">{JSON.stringify(call.input, null, 2)}</Text>
          {call.result && (
            <>
              <Text color="cyan">[Tool Result]</Text>
              <Text color="gray">{call.result}</Text>
            </>
          )}
          {!call.result && (
            <Text color="yellow">
              <Spinner type="dots" /> Executing...
            </Text>
          )}
        </Box>
      ))}

      {/* Bottom separator */}
      <Text color="gray">{'─'.repeat(60)}</Text>
    </Box>
  );
});

export const MessageList: React.FC<MessageListProps> = memo(({messages, isLoading, streamingContent}) => {
  return (
    <Box flexDirection="column">
      {messages.map((message, index) => (
        <Message key={index} message={message} />
      ))}

      {/* Show streaming content */}
      {streamingContent && (
        <StreamingMessageComponent content={streamingContent} />
      )}

      {/* Loading indicator (only show if not streaming) */}
      {isLoading && !streamingContent && (
        <Box>
          <Text color="blue">
            <Spinner type="dots" />
          </Text>
          <Text color="blue"> Thinking...</Text>
        </Box>
      )}
    </Box>
  );
});