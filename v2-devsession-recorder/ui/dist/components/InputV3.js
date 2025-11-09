import React, { useState, useEffect, useRef } from 'react';
import { Box, Text, useInput, useStdin } from 'ink';
export const SmartInput = ({ onSubmit, isDisabled = false }) => {
    const [inputValue, setInputValue] = useState('');
    const [cursorPosition, setCursorPosition] = useState(0);
    const [pasteBuffer, setPasteBuffer] = useState('');
    const [showPasteAnnotation, setShowPasteAnnotation] = useState(false);
    const pasteAccumulatorRef = useRef('');
    const pasteTimerRef = useRef(null);
    const { stdin, setRawMode } = useStdin();
    useEffect(() => {
        setRawMode(true);
        return () => {
            setRawMode(false);
        };
    }, [setRawMode]);
    useInput((input, key) => {
        if (isDisabled)
            return;
        // DEBUG: Log every input event
        if (input || key.return) {
            console.error(`[InputV3] input.length=${input?.length || 0}, key.return=${key.return}, pasteBuffer.length=${pasteBuffer.length}, showPasteAnnotation=${showPasteAnnotation}`);
        }
        // Handle paste annotation display
        if (showPasteAnnotation && key.return) {
            const lines = pasteBuffer.split('\n').length;
            const chars = pasteBuffer.length;
            console.error(`[InputV3] Submitting paste: ${chars} chars, ${lines} lines`);
            let displayLines = lines;
            if (lines === 1 && chars > 80) {
                displayLines = Math.ceil(chars / 80);
            }
            const annotation = `[pasted +${displayLines} lines, ${chars.toLocaleString()} chars]`;
            onSubmit(pasteBuffer, annotation);
            setPasteBuffer('');
            setShowPasteAnnotation(false);
            setInputValue('');
            setCursorPosition(0);
            return;
        }
        if (key.return) {
            // Submit normal input
            if (inputValue.trim()) {
                onSubmit(inputValue);
                setInputValue('');
                setCursorPosition(0);
            }
        }
        else if (key.backspace || key.delete) {
            // Handle backspace
            if (cursorPosition > 0) {
                setInputValue(prev => prev.slice(0, cursorPosition - 1) + prev.slice(cursorPosition));
                setCursorPosition(prev => prev - 1);
            }
        }
        else if (key.leftArrow) {
            setCursorPosition(prev => Math.max(0, prev - 1));
        }
        else if (key.rightArrow) {
            setCursorPosition(prev => Math.min(inputValue.length, prev + 1));
        }
        else if (input) {
            // Check if this is a paste chunk (large input)
            if (input.length > 10) {
                console.error(`[InputV3] PASTE CHUNK: ${input.length} chars, accumulator: ${pasteAccumulatorRef.current.length}`);
                // Accumulate paste chunks using ref
                pasteAccumulatorRef.current += input;
                // Clear any existing timer
                if (pasteTimerRef.current) {
                    clearTimeout(pasteTimerRef.current);
                }
                // Set a new timer to finalize the paste after 300ms of no input
                pasteTimerRef.current = setTimeout(() => {
                    // Convert \r to \n (terminal sends \r instead of \n during paste)
                    const normalizedContent = (inputValue + pasteAccumulatorRef.current).replace(/\r/g, '\n');
                    const fullContent = normalizedContent;
                    const lines = fullContent.split('\n').length;
                    const chars = fullContent.length;
                    // DEBUG: Check what characters are actually in the paste
                    const hasNewlines = fullContent.includes('\n');
                    const hasCarriageReturns = fullContent.includes('\r');
                    const first50 = JSON.stringify(fullContent.substring(0, 50));
                    console.error(`[InputV3] PASTE FINALIZED: ${chars} chars, ${lines} lines`);
                    console.error(`[InputV3] Contains \\n: ${hasNewlines}, Contains \\r: ${hasCarriageReturns}`);
                    console.error(`[InputV3] First 50 chars: ${first50}`);
                    if (chars > 400 || lines > 5) {
                        // Show paste annotation
                        setPasteBuffer(fullContent);
                        setShowPasteAnnotation(true);
                    }
                    else {
                        // Small paste, treat as normal input
                        setInputValue(fullContent);
                        setCursorPosition(fullContent.length);
                    }
                    // Clear accumulator
                    pasteAccumulatorRef.current = '';
                    pasteTimerRef.current = null;
                }, 300);
                return;
            }
            // Normal single character input
            pasteAccumulatorRef.current = ''; // Clear accumulator on normal typing
            const newValue = inputValue.slice(0, cursorPosition) + input + inputValue.slice(cursorPosition);
            setInputValue(newValue);
            setCursorPosition(prev => prev + input.length);
        }
    });
    if (showPasteAnnotation) {
        const lines = pasteBuffer.split('\n').length;
        const chars = pasteBuffer.length;
        let displayLines = lines;
        if (lines === 1 && chars > 80) {
            displayLines = Math.ceil(chars / 80);
        }
        return (React.createElement(Box, null,
            React.createElement(Text, { color: "gray" }, "> "),
            React.createElement(Text, { color: "gray", backgroundColor: "darkGray" },
                "[pasted +",
                displayLines,
                " lines, ",
                chars.toLocaleString(),
                " chars]")));
    }
    return (React.createElement(Box, null,
        React.createElement(Text, { color: "gray" }, "> "),
        React.createElement(Text, null,
            inputValue,
            !isDisabled && React.createElement(Text, { inverse: true }, " "))));
};
//# sourceMappingURL=InputV3.js.map