import React, { useCallback, useEffect, useRef, useState } from 'react'
import type { TreeNode } from '../api'

interface Props {
  nodes: TreeNode[]
  selected: TreeNode | null
  onSelect: (node: TreeNode) => void
}

interface NodeProps {
  node: TreeNode
  nodeKey: string
  selected: TreeNode | null
  onSelect: (node: TreeNode) => void
  depth: number
  open: boolean
  onToggle: (key: string) => void
  openKeys: Set<string>
}

/** Build the initial set of open node keys (auto-open depth < 2). */
function buildInitialOpenKeys(nodes: TreeNode[], prefix = '', depth = 0): Set<string> {
  const keys = new Set<string>()
  nodes.forEach((node, i) => {
    const key = prefix ? `${prefix}.${i}` : String(i)
    if (node.children.length > 0 && depth < 2) {
      keys.add(key)
      buildInitialOpenKeys(node.children, key, depth + 1).forEach(k => keys.add(k))
    }
  })
  return keys
}

/** DFS-ordered list of currently visible nodes, respecting open/close state. */
function getVisibleFlat(
  nodes: TreeNode[],
  openKeys: Set<string>,
  prefix = ''
): Array<{ node: TreeNode; key: string }> {
  const result: Array<{ node: TreeNode; key: string }> = []
  nodes.forEach((node, i) => {
    const key = prefix ? `${prefix}.${i}` : String(i)
    result.push({ node, key })
    if (node.children.length > 0 && openKeys.has(key)) {
      getVisibleFlat(node.children, openKeys, key).forEach(r => result.push(r))
    }
  })
  return result
}

const TreeNodeRow: React.FC<NodeProps> = ({
  node, nodeKey, selected, onSelect, depth, open, onToggle, openKeys
}) => {
  const hasChildren = node.children.length > 0
  const isSelected = selected === node

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onSelect(node)
  }

  const handleToggle = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (hasChildren) onToggle(nodeKey)
  }

  return (
    <li>
      <div
        className={`tree-row${isSelected ? ' selected' : ''}`}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
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
          {node.type_label && <span className="tree-type-label">{node.type_label}</span>}
        </span>
      </div>
      {hasChildren && open && (
        <ul>
          {node.children.map((child, i) => {
            const childKey = `${nodeKey}.${i}`
            return (
              <TreeNodeRow
                key={i}
                node={child}
                nodeKey={childKey}
                selected={selected}
                onSelect={onSelect}
                depth={depth + 1}
                open={openKeys.has(childKey)}
                onToggle={onToggle}
                openKeys={openKeys}
              />
            )
          })}
        </ul>
      )}
    </li>
  )
}

const TreePane: React.FC<Props> = ({ nodes, selected, onSelect }) => {
  const containerRef = useRef<HTMLDivElement>(null)
  const [openKeys, setOpenKeys] = useState<Set<string>>(() => buildInitialOpenKeys(nodes))

  // Re-initialize when a new document is loaded
  useEffect(() => {
    setOpenKeys(buildInitialOpenKeys(nodes))
  }, [nodes])

  const handleToggle = useCallback((key: string) => {
    setOpenKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }, [])

  // Scroll selected row into view
  useEffect(() => {
    if (!selected || !containerRef.current) return
    const el = containerRef.current.querySelector<HTMLElement>('.tree-row.selected')
    el?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  // Arrow-key navigation
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const flat = getVisibleFlat(nodes, openKeys)
    if (flat.length === 0) return
    const currentIdx = flat.findIndex(({ node }) => node === selected)
    let nextIdx: number
    if (currentIdx === -1) {
      nextIdx = e.key === 'ArrowDown' ? 0 : flat.length - 1
    } else {
      nextIdx = e.key === 'ArrowDown' ? currentIdx + 1 : currentIdx - 1
    }
    nextIdx = Math.max(0, Math.min(flat.length - 1, nextIdx))
    if (nextIdx !== currentIdx) onSelect(flat[nextIdx].node)
  }, [nodes, openKeys, selected, onSelect])

  if (nodes.length === 0) {
    return (
      <div className="tree-empty">
        <p>No PDF loaded.</p>
        <p>Upload a PDF to inspect its structure.</p>
      </div>
    )
  }

  return (
    <div className="tree-pane" ref={containerRef} tabIndex={0} onKeyDown={handleKeyDown}>
      <ul className="tree-root">
        {nodes.map((node, i) => {
          const key = String(i)
          return (
            <TreeNodeRow
              key={i}
              node={node}
              nodeKey={key}
              selected={selected}
              onSelect={onSelect}
              depth={0}
              open={openKeys.has(key)}
              onToggle={handleToggle}
              openKeys={openKeys}
            />
          )
        })}
      </ul>
    </div>
  )
}

export default TreePane
