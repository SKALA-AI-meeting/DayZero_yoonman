import {memo} from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const components = {
 h1:({children}:React.PropsWithChildren)=> <h3>{children}</h3>,
 h2:({children}:React.PropsWithChildren)=> <h3>{children}</h3>,
 a:({href,children}:React.ComponentProps<'a'>)=> <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
 img:({alt}:React.ComponentProps<'img'>)=> <span>{alt||'이미지'}</span>,
 table:({children}:React.PropsWithChildren)=> <div className="markdown-table" role="region" aria-label="답변 표" tabIndex={0}><table>{children}</table></div>
};

export default memo(function MessageContent({text}:{text:string}){
 return <div className="message-markdown"><Markdown remarkPlugins={[remarkGfm]} components={components} skipHtml>{text}</Markdown></div>;
});
