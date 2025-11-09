import React from 'react';
import { Box, Text } from 'ink';
export const StatusBar = ({ name, tokenCount, maxTokens }) => {
    const percentage = Math.round((tokenCount / maxTokens) * 100);
    const isNearLimit = percentage > 80;
    return (React.createElement(Box, { borderStyle: "single", borderColor: "gray", paddingX: 1 },
        React.createElement(Box, { justifyContent: "space-between", width: "100%" },
            React.createElement(Text, { color: "gray" }, name),
            React.createElement(Box, null,
                React.createElement(Text, { color: isNearLimit ? 'yellow' : 'gray' },
                    tokenCount.toLocaleString(),
                    "/",
                    maxTokens.toLocaleString(),
                    " tokens"),
                React.createElement(Text, { color: isNearLimit ? 'yellow' : 'gray' },
                    " (",
                    percentage,
                    "%)")))));
};
//# sourceMappingURL=Status.js.map