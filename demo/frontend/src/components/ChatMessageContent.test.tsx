import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ChatMessageContent } from './ChatMessageContent';


describe('ChatMessageContent', () => {
  it('renders model markdown as readable chat content', () => {
    render(
      <ChatMessageContent>{`**Course Learning Outcomes** [1]:
* **CLO1**: Explain digital computer structure.
* **CLO2**: Describe processor evolution.

Use \`CEA201\` as the course code.`}</ChatMessageContent>,
    );

    expect(screen.getByRole('heading', { name: /Course Learning Outcomes/ })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('CLO1').tagName).toBe('STRONG');
    expect(screen.getByText('[1]')).toHaveClass('chat-message-citation');
    expect(screen.getByText('CEA201').tagName).toBe('CODE');
    expect(screen.getByLabelText('Assistant response')).not.toHaveTextContent('**');
  });

  it('never interprets model-provided HTML as DOM', () => {
    const { container } = render(<ChatMessageContent>{'<img src=x onerror=alert(1)> hello'}</ChatMessageContent>);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument();
  });
});
