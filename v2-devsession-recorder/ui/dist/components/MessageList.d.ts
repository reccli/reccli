import React from 'react';
interface Message {
    role: 'user' | 'assistant' | 'system';
    content: string;
    annotation?: string;
}
interface MessageListProps {
    messages: Message[];
    isLoading: boolean;
}
declare const Message: React.MemoExoticComponent<({ message }: {
    message: Message;
}) => React.JSX.Element>;
export declare const MessageList: React.FC<MessageListProps>;
export {};
//# sourceMappingURL=MessageList.d.ts.map