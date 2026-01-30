import React from 'react';
import {Box, Text} from 'ink';

interface StatusBarProps {
  name: string;
  tokenCount: number;
  maxTokens: number;
}

export const StatusBar: React.FC<StatusBarProps> = ({name, tokenCount, maxTokens}) => {
  const percentage = Math.round((tokenCount / maxTokens) * 100);
  const isNearLimit = percentage > 80;

  return (
    <Box borderStyle="single" borderColor="gray" paddingX={1}>
      <Box justifyContent="space-between" width="100%">
        <Text color="gray">{name}</Text>
        <Box>
          <Text color={isNearLimit ? 'yellow' : 'gray'}>
            {tokenCount.toLocaleString()}/{maxTokens.toLocaleString()} tokens
          </Text>
          <Text color={isNearLimit ? 'yellow' : 'gray'}> ({percentage}%)</Text>
        </Box>
      </Box>
    </Box>
  );
};