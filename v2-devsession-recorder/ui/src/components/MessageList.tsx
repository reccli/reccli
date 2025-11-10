import React, {memo} from 'react';
import {Box, Text} from 'ink';
import Spinner from 'ink-spinner';

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  annotation?: string;
}

interface MessageListProps {
  messages: Message[];
  isLoading: boolean;
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

export const MessageList: React.FC<MessageListProps> = memo(({messages, isLoading}) => {
  return (
    <Box flexDirection="column">
      {messages.map((message, index) => (
        <Message key={index} message={message} />
      ))}

      {/* Loading indicator */}
      {isLoading && (
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