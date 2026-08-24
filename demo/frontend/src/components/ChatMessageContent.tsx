import React from 'react';

interface ChatMessageContentProps {
  children: string;
  className?: string;
}

const INLINE_TOKEN = /(\*\*[^*]+\*\*|`[^`]+`|\[\d+\])/g;
const UNORDERED_ITEM = /^\s*[-*]\s+(.+)$/;
const ORDERED_ITEM = /^\s*\d+[.)]\s+(.+)$/;
const MARKDOWN_HEADING = /^\s*(#{1,4})\s+(.+)$/;
const EMPHATIC_HEADING = /^\s*\*\*(.+?)\*\*(\s*\[\d+\])?:?\s*$/;

function inlineContent(value: string, keyPrefix: string): React.ReactNode[] {
  return value.split(INLINE_TOKEN).filter(Boolean).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={key}>{part.slice(1, -1)}</code>;
    }
    if (/^\[\d+\]$/.test(part)) {
      return <span className="chat-message-citation" key={key}>{part}</span>;
    }
    return <React.Fragment key={key}>{part}</React.Fragment>;
  });
}

function isBlockStart(line: string): boolean {
  return !line.trim()
    || MARKDOWN_HEADING.test(line)
    || EMPHATIC_HEADING.test(line)
    || UNORDERED_ITEM.test(line)
    || ORDERED_ITEM.test(line);
}

export const ChatMessageContent: React.FC<ChatMessageContentProps> = ({ children, className }) => {
  const lines = children.replace(/\r\n?/g, '\n').split('\n');
  const blocks: React.ReactNode[] = [];

  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const markdownHeading = line.match(MARKDOWN_HEADING);
    const emphaticHeading = line.match(EMPHATIC_HEADING);
    if (markdownHeading || emphaticHeading) {
      if (markdownHeading) {
        blocks.push(<h4 key={`heading-${index}`}>{inlineContent(markdownHeading[2], `heading-${index}`)}</h4>);
      } else if (emphaticHeading) {
        const citation = emphaticHeading[2]?.trim();
        blocks.push(
          <h4 key={`heading-${index}`}>
            {emphaticHeading[1]}
            {citation && <span className="chat-message-citation">{citation}</span>}
          </h4>,
        );
      }
      index += 1;
      continue;
    }

    const unordered = line.match(UNORDERED_ITEM);
    if (unordered) {
      const items: string[] = [];
      while (index < lines.length) {
        const match = lines[index].match(UNORDERED_ITEM);
        if (!match) break;
        items.push(match[1]);
        index += 1;
      }
      blocks.push(
        <ul key={`list-${index}`}>
          {items.map((item, itemIndex) => <li key={itemIndex}>{inlineContent(item, `ul-${index}-${itemIndex}`)}</li>)}
        </ul>,
      );
      continue;
    }

    const ordered = line.match(ORDERED_ITEM);
    if (ordered) {
      const items: string[] = [];
      while (index < lines.length) {
        const match = lines[index].match(ORDERED_ITEM);
        if (!match) break;
        items.push(match[1]);
        index += 1;
      }
      blocks.push(
        <ol key={`ordered-${index}`}>
          {items.map((item, itemIndex) => <li key={itemIndex}>{inlineContent(item, `ol-${index}-${itemIndex}`)}</li>)}
        </ol>,
      );
      continue;
    }

    const paragraph: string[] = [line.trim()];
    index += 1;
    while (index < lines.length && !isBlockStart(lines[index])) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`paragraph-${index}`}>{inlineContent(paragraph.join(' '), `paragraph-${index}`)}</p>);
  }

  return (
    <div className={`chat-message-content${className ? ` ${className}` : ''}`} aria-label="Assistant response">
      {blocks}
    </div>
  );
};
