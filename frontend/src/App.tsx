import { useCallback, useMemo, useState } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import type { TreeNode, UploadResponse } from './api'
import { uploadPdf } from './api'
import DetailPane from './components/DetailPane'
import Toolbar from './components/Toolbar'
import TreePane from './components/TreePane'

function buildObjMap(node: TreeNode, map: Map<number, TreeNode>, depth = 0) {
  // Skip stub reference nodes (detail = "Reference: N G R\n\n...") so they
  // never overwrite the real object entries (e.g. XRef Table > obj N).
  if (node.obj_num >= 0 && !node.detail.startsWith('Reference:')) {
    map.set(node.obj_num, node)
  }
  // Map named section nodes to sentinel keys for backref navigation
  if (depth === 1) {
    if (node.label === 'Trailer')   map.set(-1, node)
    if (node.label === 'Catalog')   map.set(-2, node)
    if (node.label === 'Page Tree') map.set(-3, node)
    if (node.label === 'Info')      map.set(-4, node)
  }
  for (const child of node.children) buildObjMap(child, map, depth + 1)
}

function buildParentMap(node: TreeNode, map: Map<TreeNode, TreeNode>, parent: TreeNode | null = null) {
  if (parent) map.set(node, parent)
  for (const child of node.children) buildParentMap(child, map, node)
}

function App() {
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [filename, setFilename] = useState<string | null>(null)
  const [version, setVersion] = useState<string | null>(null)
  const [rootNode, setRootNode] = useState<TreeNode | null>(null)
  const [selected, setSelected] = useState<TreeNode | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const objMap = useMemo(() => {
    const map = new Map<number, TreeNode>()
    if (rootNode) buildObjMap(rootNode, map)
    return map
  }, [rootNode])

  const parentMap = useMemo(() => {
    const map = new Map<TreeNode, TreeNode>()
    if (rootNode) buildParentMap(rootNode, map)
    return map
  }, [rootNode])

  const selectedChain = useMemo(() => {
    if (!selected) return []
    const chain: TreeNode[] = []
    let cur: TreeNode | undefined = selected
    while (cur) {
      chain.unshift(cur)
      cur = parentMap.get(cur)
    }
    return chain
  }, [selected, parentMap])

  const applyResponse = useCallback((resp: UploadResponse) => {
    setUploadId(resp.id)
    setFilename(resp.filename)
    setVersion(resp.version)
    setRootNode(resp.tree)
    setSelected(null)
  }, [])

  const handleFile = useCallback(async (file: File) => {
    setLoading(true)
    setError(null)
    setSelected(null)
    setRootNode(null)
    try {
      const resp = await uploadPdf(file)
      applyResponse(resp)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [applyResponse])

  const handleOpen = useCallback((resp: UploadResponse) => {
    setError(null)
    applyResponse(resp)
  }, [applyResponse])

  const handleJumpToObj = useCallback((num: number) => {
    const node = objMap.get(num)
    if (node) setSelected(node)
  }, [objMap])

  const treeNodes: TreeNode[] = rootNode ? [rootNode] : []

  return (
    <div className="app">
      <Toolbar
        filename={filename}
        version={version}
        loading={loading}
        onFile={handleFile}
        onOpen={handleOpen}
      />

      {error && (
        <div className="app-error">
          ⚠ {error}
          <button onClick={() => setError(null)} className="btn-dismiss">✕</button>
        </div>
      )}

      <div className="workspace">
        <PanelGroup direction="horizontal" autoSaveId="main-split">
          <Panel defaultSize={30} minSize={15} className="panel-left">
            <TreePane
              nodes={treeNodes}
              selected={selected}
              onSelect={setSelected}
            />
          </Panel>

          <PanelResizeHandle className="resize-handle" />

          <Panel minSize={30} className="panel-right">
            <DetailPane node={selected} chain={selectedChain} uploadId={uploadId} onJumpToObj={handleJumpToObj} onSelect={setSelected} />
          </Panel>
        </PanelGroup>
      </div>
    </div>
  )
}

export default App
