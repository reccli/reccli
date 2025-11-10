import React, { useState, useEffect, useRef } from 'react';
import { Box, Text, useInput, useStdin } from 'ink';
export const SmartInput = ({ onSubmit, isDisabled = false }) => {
    const [inputValue, setInputValue] = useState('');
    const [cursorPosition, setCursorPosition] = useState(0);
    const [pasteBuffer, setPasteBuffer] = useState(''); // Hidden paste content
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
        if (key.return) {
            // Submit: if we have pasteBuffer, check if annotation is still intact
            if (pasteBuffer) {
                const lines = pasteBuffer.split('\n').length;
                const chars = pasteBuffer.length;
                let displayLines = lines;
                if (lines === 1 && chars > 80) {
                    displayLines = Math.ceil(chars / 80);
                }
                const expectedAnnotation = `[pasted +${displayLines} lines, ${chars.toLocaleString()} chars]`;
                // Check if the annotation is still intact in inputValue
                if (inputValue.includes(expectedAnnotation)) {
                    // Annotation intact - submit with pasteBuffer
                    const fullContent = inputValue.replace(expectedAnnotation, '') + pasteBuffer;
                    onSubmit(fullContent, expectedAnnotation);
                }
                else {
                    // Annotation broken - just submit inputValue without pasteBuffer
                    onSubmit(inputValue);
                }
            }
            else {
                onSubmit(inputValue);
            }
            setInputValue('');
            setCursorPosition(0);
            setPasteBuffer('');
        }
        else if (key.backspace || key.delete) {
            // Delete character from inputValue
            if (cursorPosition > 0) {
                const newValue = inputValue.slice(0, cursorPosition - 1) + inputValue.slice(cursorPosition);
                setInputValue(newValue);
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
                // Accumulate paste chunks using ref
                pasteAccumulatorRef.current += input;
                // Clear any existing timer
                if (pasteTimerRef.current) {
                    clearTimeout(pasteTimerRef.current);
                }
                // Set a new timer to finalize the paste after 100ms of no input
                pasteTimerRef.current = setTimeout(() => {
                    // Convert \r to \n (terminal sends \r instead of \n during paste)
                    const normalizedContent = pasteAccumulatorRef.current.replace(/\r/g, '\n');
                    const lines = normalizedContent.split('\n').length;
                    const chars = normalizedContent.length;
                    if (chars > 400 || lines > 5) {
                        // Large paste - add annotation to inputValue, store content in pasteBuffer
                        let displayLines = lines;
                        if (lines === 1 && chars > 80) {
                            displayLines = Math.ceil(chars / 80);
                        }
                        const annotation = `[pasted +${displayLines} lines, ${chars.toLocaleString()} chars]`;
                        setInputValue(prev => prev + annotation);
                        setCursorPosition(prev => prev + annotation.length);
                        setPasteBuffer(normalizedContent);
                    }
                    else {
                        // Small paste, add to inputValue directly (no annotation)
                        setInputValue(prev => prev + normalizedContent);
                        setCursorPosition(prev => prev + normalizedContent.length);
                    }
                    // Clear accumulator
                    pasteAccumulatorRef.current = '';
                    pasteTimerRef.current = null;
                }, 100);
                return;
            }
            // Normal single character input
            // If there's an active paste timer, this might be the last char of a paste
            if (pasteTimerRef.current) {
                pasteAccumulatorRef.current += input;
                clearTimeout(pasteTimerRef.current);
                pasteTimerRef.current = setTimeout(() => {
                    const normalizedContent = pasteAccumulatorRef.current.replace(/\r/g, '\n');
                    const lines = normalizedContent.split('\n').length;
                    const chars = normalizedContent.length;
                    if (chars > 400 || lines > 5) {
                        let displayLines = lines;
                        if (lines === 1 && chars > 80) {
                            displayLines = Math.ceil(chars / 80);
                        }
                        const annotation = `[pasted +${displayLines} lines, ${chars.toLocaleString()} chars]`;
                        setInputValue(prev => prev + annotation);
                        setCursorPosition(prev => prev + annotation.length);
                        setPasteBuffer(normalizedContent);
                    }
                    else {
                        setInputValue(prev => prev + normalizedContent);
                        setCursorPosition(prev => prev + normalizedContent.length);
                    }
                    pasteAccumulatorRef.current = '';
                    pasteTimerRef.current = null;
                }, 100);
                return;
            }
            // True single character input - clear accumulator and add to inputValue
            pasteAccumulatorRef.current = '';
            const newValue = inputValue.slice(0, cursorPosition) + input + inputValue.slice(cursorPosition);
            setInputValue(newValue);
            setCursorPosition(prev => prev + input.length);
        }
    });
    return (React.createElement(Box, null,
        React.createElement(Text, { color: "gray" }, "> "),
        React.createElement(Text, null,
            inputValue.slice(0, cursorPosition),
            !isDisabled && React.createElement(Text, { inverse: true }, " "),
            inputValue.slice(cursorPosition))));
};
//# sourceMappingURL=InputV3.js.map