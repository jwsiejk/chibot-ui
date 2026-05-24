import React, { FormEvent, useState } from 'react';

export const TypedInput = ({ onSubmitText, disabled }: { onSubmitText: (text: string) => void | Promise<void>; disabled?: boolean }) => {
  const [text, setText] = useState('');

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled) {
      return;
    }

    onSubmitText(trimmed);
    setText('');
  };

  return (
    <form onSubmit={onSubmit} aria-label="typed input form">
      <label htmlFor="typed-input">Type a message</label>
      <input id="typed-input" value={text} onChange={(event) => setText(event.target.value)} disabled={disabled} />
      <button type="submit" disabled={disabled}>Send</button>
    </form>
  );
};
