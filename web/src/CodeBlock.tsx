import { useState } from 'react'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark, oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import markup from 'react-syntax-highlighter/dist/esm/languages/prism/markup'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript'
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml'

SyntaxHighlighter.registerLanguage('bash', bash)
SyntaxHighlighter.registerLanguage('css', css)
SyntaxHighlighter.registerLanguage('javascript', javascript)
SyntaxHighlighter.registerLanguage('json', json)
SyntaxHighlighter.registerLanguage('markup', markup)
SyntaxHighlighter.registerLanguage('python', python)
SyntaxHighlighter.registerLanguage('sql', sql)
SyntaxHighlighter.registerLanguage('typescript', typescript)
SyntaxHighlighter.registerLanguage('yaml', yaml)

type CodeBlockProps = {
  className?: string
  code: string
}

export default function CodeBlock({ className, code }: CodeBlockProps) {
  const [copied, setCopied] = useState(false)
  const language = className?.replace('language-', '') ?? 'text'
  const isDark = document.documentElement.classList.contains('dark')

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-950 dark:border-slate-700">
      <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-3 py-2 text-[11px] uppercase tracking-[0.18em] text-slate-400">
        <span>{language}</span>
        <button type="button" onClick={handleCopy} className="text-slate-300 hover:text-white">
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <SyntaxHighlighter
        language={language}
        style={isDark ? oneDark : oneLight}
        customStyle={{ margin: 0, padding: '1rem', background: 'transparent', fontSize: '0.8rem' }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}
