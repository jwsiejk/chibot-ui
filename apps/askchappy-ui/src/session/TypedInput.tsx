import React, { FormEvent, useState } from 'react';

export const TypedInput = ({ onSubmitText }: { onSubmitText: (text: string) => void }) => {
  const [text, setText] = useState('');

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) {
      return;
    }

    onSubmitText(trimmed);
    setText('');
  };

  return (
    <form onSubmit={onSubmit} aria-label="typed input form">
      <label htmlFor="typed-input">Type a message</label>
      <input id="typed-input" value={text} onChange={(event) => setText(event.target.value)} />
      <button type="submit">Send</button>
    </form>
  );
};
