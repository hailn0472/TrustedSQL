import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { fc, test } from '@fast-check/vitest';
import Message from './Message';

/**
 * Feature: frontend-ui-rebuild, Property 1: Message visual consistency
 * 
 * For any message in the message list, the message should have styling that matches its type:
 * - User messages should have a purple gradient background (from-[#5E507F] to-[#4A3F71]), 
 *   white text, right alignment, rounded-br-none styling, and shadow-md
 * - AI messages should have a beige background (#F3F3EE), dark text (text-gray-800), 
 *   left alignment, rounded-bl-none styling, and shadow-sm
 * 
 * Validates: Requirements 1.3, 3.1, 3.2, 3.3
 */

describe('Message Component - Property-Based Tests', () => {
  test.prop([
    fc.record({
      id: fc.integer({ min: 1, max: 10000 }),
      role: fc.constantFrom('user', 'assistant'),
      content: fc.string({ minLength: 1, maxLength: 500 }),
      timestamp: fc.date(),
      isStreaming: fc.boolean(),
      isLoading: fc.constant(false), // Exclude loading state for visual consistency test
    })
  ], { numRuns: 100 })(
    'should apply consistent visual styling based on message role',
    (message) => {
      const { container } = render(<Message message={message} />);
      
      const isUserMessage = message.role === 'user';
      
      // Get the outer flex container
      const outerDiv = container.firstChild;
      expect(outerDiv).toBeInTheDocument();
      
      // Get the message bubble (inner div)
      const messageBubble = outerDiv.firstChild;
      expect(messageBubble).toBeInTheDocument();
      
      if (isUserMessage) {
        // User message assertions
        
        // Check right alignment
        expect(outerDiv.className).toContain('justify-end');
        
        // Check purple gradient background
        expect(messageBubble.className).toContain('bg-gradient-to-br');
        expect(messageBubble.className).toContain('from-[#5E507F]');
        expect(messageBubble.className).toContain('to-[#4A3F71]');
        
        // Check white text
        expect(messageBubble.className).toContain('text-white');
        
        // Check rounded-br-none (bottom-right corner not rounded)
        expect(messageBubble.className).toContain('rounded-br-none');
        
        // Check shadow-md
        expect(messageBubble.className).toContain('shadow-md');
      } else {
        // AI message assertions
        
        // Check left alignment
        expect(outerDiv.className).toContain('justify-start');
        
        // Check beige background
        expect(messageBubble.className).toContain('bg-[#F3F3EE]');
        
        // Check dark text
        expect(messageBubble.className).toContain('text-gray-800');
        
        // Check rounded-bl-none (bottom-left corner not rounded)
        expect(messageBubble.className).toContain('rounded-bl-none');
        
        // Check shadow-sm
        expect(messageBubble.className).toContain('shadow-sm');
      }
      
      // Common styling that should be present for both types
      expect(messageBubble.className).toContain('py-3');
      expect(messageBubble.className).toContain('px-5');
      expect(messageBubble.className).toContain('rounded-lg');
    }
  );
});
