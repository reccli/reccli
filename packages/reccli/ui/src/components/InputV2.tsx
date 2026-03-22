import React, {useState, useRef, useEffect} from 'react';
import {Box, Text, useInput} from 'ink';
import TextInput from 'ink-text-input';

interface SmartInputProps {
  onSubmit: (text: string, annotation?: string) => void;
  isDisabled?: boolean;
}

export const SmartInput: React.FC<SmartInputProps> = ({onSubmit, isDisabled = false}) => {
  const [inputValue, setInputValue] = useState('');
  const [pasteState, setPasteState] = useState<{
    active: boolean;
    annotation: string;
    content: string;
  }>({active: false, annotation: '', content: ''});

  const previousLength = useRef(0);

  // Handle Enter key when paste annotation is shown
  useInput((input, key) => {
    if (pasteState.active && key.return) {
      onSubmit(pasteState.content, pasteState.annotation);
      setPasteState({active: false, annotation: '', content: ''});
      setInputValue('');
    }
  });

  const handleChange = (newValue: string) => {
    const lengthDiff = newValue.length - previousLength.current;

    // Detect large paste (more than 10 chars added at once)
    if (lengthDiff > 10) {
      const lines = newValue.split('\n').length;
      const chars = newValue.length;

      if (chars > 400 || lines > 5) {
        // Large paste detected - show annotation only
        // Better line counting: if no newlines but long text, estimate lines based on width
        let displayLines = lines;
        if (lines === 1 && chars > 80) {
          // Estimate wrapped lines (assuming ~80 chars per line in terminal)
          displayLines = Math.ceil(chars / 80);
        }
        setPasteState({
          active: true,
          annotation: `[pasted +${displayLines} lines, ${chars.toLocaleString()} chars]`,
          content: newValue
        });
        // Don't update input value to prevent display
        return;
      }
    }

    // Normal typing or small paste - allow it
    setInputValue(newValue);
    previousLength.current = newValue.length;
  };

  const handleSubmit = () => {
    if (!pasteState.active && inputValue) {
      onSubmit(inputValue);
      setInputValue('');
      previousLength.current = 0;
    }
  };

  // Update previous length when input changes
  useEffect(() => {
    if (!pasteState.active) {
      previousLength.current = inputValue.length;
    }
  }, [inputValue, pasteState.active]);

  return (
    <Box>
      <Text color="gray">{"> "}</Text>
      {pasteState.active ? (
        // Show only annotation when paste detected
        <Text color="gray" backgroundColor="darkGray">
          {pasteState.annotation}
        </Text>
      ) : (
        // Normal text input
        <TextInput
          value={inputValue}
          onChange={handleChange}
          onSubmit={handleSubmit}
          placeholder={isDisabled ? "Thinking..." : "Type your message..."}
        />
      )}
    </Box>
  );
};