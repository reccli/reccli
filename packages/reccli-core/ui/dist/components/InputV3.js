import React, { useState, useEffect, useRef } from 'react';
import { Box, Text, useInput, useStdin } from 'ink';
export const SmartInput = ({ onSubmit, onCancel, isDisabled = false }) => {
    const [inputValue, setInputValue] = useState('');
    const [cursorPosition, setCursorPosition] = useState(0);
    const [pasteBuffer, setPasteBuffer] = useState(''); // Hidden paste content
    const [history, setHistory] = useState([]);
    const [historyIndex, setHistoryIndex] = useState(-1);
    const [savedInput, setSavedInput] = useState(''); // Save current input when entering history
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
        // Escape key cancels LLM request (even if disabled/loading)
        if (key.escape && onCancel) {
            onCancel();
            return;
        }
        if (isDisabled)
            return;
        if (key.return) {
            // Submit: if we have pasteBuffer, check if annotation is still intact
            if (pasteBuffer) {
                console.error(`[InputV3] Submitting with pasteBuffer: ${pasteBuffer.length} chars`);
                console.error(`[InputV3] pasteBuffer first 100: ${pasteBuffer.substring(0, 100)}`);
                console.error(`[InputV3] pasteBuffer last 100: ${pasteBuffer.substring(pasteBuffer.length - 100)}`);
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
                    console.error(`[InputV3] Annotation intact, fullContent: ${fullContent.length} chars`);
                    onSubmit(fullContent, expectedAnnotation);
                }
                else {
                    // Annotation broken - just submit inputValue without pasteBuffer
                    console.error(`[InputV3] Annotation broken, submitting inputValue only`);
                    onSubmit(inputValue);
                }
            }
            else {
                onSubmit(inputValue);
            }
            // Add to history if not empty
            if (inputValue.trim()) {
                setHistory(prev => [...prev, inputValue]);
            }
            setInputValue('');
            setCursorPosition(0);
            setPasteBuffer('');
            setHistoryIndex(-1);
            setSavedInput('');
        }
        else if (key.upArrow) {
            // Navigate up in history
            if (history.length > 0) {
                // First time entering history - save current input
                if (historyIndex === -1) {
                    setSavedInput(inputValue);
                    setHistoryIndex(history.length - 1);
                    setInputValue(history[history.length - 1]);
                    setCursorPosition(history[history.length - 1].length);
                }
                else if (historyIndex > 0) {
                    // Go further back in history
                    const newIndex = historyIndex - 1;
                    setHistoryIndex(newIndex);
                    setInputValue(history[newIndex]);
                    setCursorPosition(history[newIndex].length);
                }
            }
        }
        else if (key.downArrow) {
            // Navigate down in history
            if (historyIndex >= 0) {
                const newIndex = historyIndex + 1;
                if (newIndex >= history.length) {
                    // Back to saved input (what you were typing)
                    setHistoryIndex(-1);
                    setInputValue(savedInput);
                    setCursorPosition(savedInput.length);
                }
                else {
                    setHistoryIndex(newIndex);
                    setInputValue(history[newIndex]);
                    setCursorPosition(history[newIndex].length);
                }
            }
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
                // Set a new timer to finalize the paste after 300ms of no input
                pasteTimerRef.current = setTimeout(() => {
                    console.error(`[InputV3] Finalizing paste, accumulator: ${pasteAccumulatorRef.current.length} chars`);
                    // Convert \r to \n (terminal sends \r instead of \n during paste)
                    const normalizedContent = pasteAccumulatorRef.current.replace(/\r/g, '\n');
                    const lines = normalizedContent.split('\n').length;
                    const chars = normalizedContent.length;
                    console.error(`[InputV3] After normalization: ${chars} chars, ${lines} lines`);
                    console.error(`[InputV3] First 100 chars: ${normalizedContent.substring(0, 100)}`);
                    console.error(`[InputV3] Last 100 chars: ${normalizedContent.substring(chars - 100)}`);
                    if (chars > 400 || lines > 5) {
                        // Large paste - add annotation to inputValue, store content in pasteBuffer
                        let displayLines = lines;
                        if (lines === 1 && chars > 80) {
                            displayLines = Math.ceil(chars / 80);
                        }
                        const annotation = `[pasted +${displayLines} lines, ${chars.toLocaleString()} chars]`;
                        console.error(`[InputV3] Setting pasteBuffer to ${chars} chars`);
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
            // If there's an active paste timer OR accumulator has content, this might be part of a paste
            if (pasteTimerRef.current || pasteAccumulatorRef.current.length > 0) {
                console.error(`[InputV3] Small chunk, timer=${!!pasteTimerRef.current}, accumulator=${pasteAccumulatorRef.current.length} chars`);
                pasteAccumulatorRef.current += input;
                // Clear existing timer if any
                if (pasteTimerRef.current) {
                    clearTimeout(pasteTimerRef.current);
                }
                // Set new timer (300ms to catch all chunks including trailing chars)
                pasteTimerRef.current = setTimeout(() => {
                    console.error(`[InputV3] Small chunk timer fired, accumulator=${pasteAccumulatorRef.current.length} chars`);
                    const normalizedContent = pasteAccumulatorRef.current.replace(/\r/g, '\n');
                    const lines = normalizedContent.split('\n').length;
                    const chars = normalizedContent.length;
                    if (chars > 400 || lines > 5) {
                        let displayLines = lines;
                        if (lines === 1 && chars > 80) {
                            displayLines = Math.ceil(chars / 80);
                        }
                        const annotation = `[pasted +${displayLines} lines, ${chars.toLocaleString()} chars]`;
                        console.error(`[InputV3] Setting pasteBuffer from small chunk: ${chars} chars`);
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
                }, 300);
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