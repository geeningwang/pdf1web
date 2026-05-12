import React, { useEffect, useRef, useState } from 'react'
import type { TreeNode } from '../api'

interface Props {
  nodes: TreeNode[]
  selected: TreeNode | null
  onSelect: (node: TreeNode) => void
}

interface NodeProps {
  node: TreeNode
  selected: TreeNode | null
  onSelect: (node: TreeNode) => void
  depth: number
}

const TreeNodeRow: React.FC<NodeProps> = ({ node, selected, onSelect, depth }) => {
  const [open, setOpen] = useState(depth < 2)

  const hasChildren = node.children.length > 0
  const isSelected = selected === node

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onSelect(node)
  }

  const handleToggle = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (hasChildren) setOpen(o => !o)
  }

  return (
    <li>
      <div
        className={`tree-row${isSelected ? ' selected' : ''}`}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
        data-obj-num={node.obj_num}
        onClick={handleClick}
      >
        <span
          className="tree-toggle"
          onClick={handleToggle}
          aria-label={open ? 'collapse' : 'expand'}
        >
          {hasChildren ? (open ? '▾' : '▸') : ' '}
        </span>
        <span className="tree-label" title={node.label}>
          {node.is_image && <span className="tree-image-badge" title="Image XObject">🖼</span>}
          {node.label}
        </span>
      </div>
      {hasChildren && open && (
        <ul>
          {node.children.map((child, i) => (
            <TreeNodeRow
              key={i}
              node={child}
              selected={selected}
              onSelect={onSelect}
              depth={depth + 1}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

const TreePane: React.FC<Props> = ({ nodes, selected, onSelect }) => {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!selected || !containerRef.current) return
    const el = containerRef.current.querySelector<HTMLElement>(
      `[data-obj-num="${selected.obj_num}"]`
    )
    el?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  if (nodes.length === 0) {
    return (
      <div className="tree-empty">
        <p>No PDF loaded.</p>
        <p>Upload a PDF to inspect its structure.</p>
      </div>
    )
  }

  return (
    <div className="tree-pane" ref={containerRef}>
      <ul className="tree-root">
        {nodes.map((node, i) => (
          <TreeNodeRow
            key={i}
            node={node}
            selected={selected}
            onSelect={onSelect}
            depth={0}
          />
        ))}
      </ul>
    </div>
  )
}

export default TreePane
