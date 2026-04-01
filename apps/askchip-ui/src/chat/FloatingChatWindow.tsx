import { ChatPanel, type ChatPanelCoreProps } from './ChatPanel';

interface FloatingChatWindowProps extends ChatPanelCoreProps {
  open: boolean;
}

export function FloatingChatWindow({ open, ...props }: FloatingChatWindowProps) {
  return <ChatPanel mode="floating" open={open} {...props} />;
}
