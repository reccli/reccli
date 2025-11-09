import React, { useState } from 'react';
import { Box, Text, useInput } from 'ink';
import TextInput from 'ink-text-input';
export const SmartInput = ({ onSubmit, isDisabled = false }) => {
    const [value, setValue] = useState('');
    const [pasteAnnotation, setPasteAnnotation] = useState('');
    const [actualContent, setActualContent] = useState('');
    const [lastInputTime, setLastInputTime] = useState(Date.now());
    const PASTE_THRESHOLD = 50; // 50ms between chars = likely paste
    // Handle Enter key when paste annotation is shown
    useInput((input, key) => {
        if (pasteAnnotation && key.return) {
            onSubmit(actualContent, pasteAnnotation);
            // Reset state
            setPasteAnnotation('');
            setActualContent('');
            setValue('');
        }
    });
    const handleChange = (newValue) => {
        const now = Date.now();
        const timeDiff = now - lastInputTime;
        const charDiff = newValue.length - value.length;
        // Detect paste: multiple chars added at once
        if (charDiff > 10) {
            // This is definitely a paste
            const lines = newValue.split('\n').length;
            const chars = newValue.length;
            if (chars > 400 || lines > 5) {
                // Large paste detected - show annotation instead of content
                setPasteAnnotation(`[pasted +${lines} lines, ${chars.toLocaleString()} chars]`);
                setActualContent(newValue);
                setValue(pasteAnnotation); // Show annotation in input field
                return;
            }
        }
        // Normal typing or small paste
        setLastInputTime(now);
        setValue(newValue);
    };
    const handleSubmit = () => {
        if (pasteAnnotation) {
            // Already handled by useInput hook
            return;
        }
        // Normal submit
        onSubmit(value);
        setValue('');
    };
    return (React.createElement(Box, null,
        React.createElement(Text, { color: "gray" }, "> "),
        pasteAnnotation ? (
        // Show annotation for paste - static text, waiting for Enter
        React.createElement(Text, { color: "gray", backgroundColor: "darkGray" }, pasteAnnotation)) : (
        // Normal input
        React.createElement(TextInput, { value: value, onChange: handleChange, onSubmit: handleSubmit, placeholder: isDisabled ? "Thinking..." : "Type your message..." }))));
};
//# sourceMappingURL=Input.js.map