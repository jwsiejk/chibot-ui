import React, { FormEvent, useState } from 'react';

export const TypedInput = ({ onSubmitText, disabled, compact = false }: { onSubmitText: (text: string) => void | Promise<void>; disabled?: boolean; compact?: boolean }) => {
  const [text, setText] = useState('');

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSubmitText(trimmed);
    setText('');
  };

  if (compact) {
    return (
      <form className="meeting-composer" onSubmit={onSubmit} aria-label="typed input form">
        <label htmlFor="typed-input" className="sr-only">Type a message</label>
        <input className="input" id="typed-input" placeholder="Message Chappy" value={text} onChange={(event) => setText(event.target.value)} disabled={disabled} />
        <button className="meeting-btn" type="submit" disabled={disabled}>Send</button>
      </form>
    );
  }

  return (
    <form className="card panel" onSubmit={onSubmit} aria-label="typed input form">
      <label htmlFor="typed-input">Type a message</label>
      <div className="composer">
        <input className="input" id="typed-input" value={text} onChange={(event) => setText(event.target.value)} disabled={disabled} />
        <button className="btn" type="submit" disabled={disabled}>Send</button>
      </div>
    </form>
  );
};
