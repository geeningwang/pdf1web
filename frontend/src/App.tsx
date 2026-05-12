import { useCallback, useMemo, useState } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import type { TreeNode } from './api'
import { uploadPdf } from './api'
import DetailPane from './components/DetailPane'
import Toolbar from './components/Toolbar'
import TreePane from './components/TreePane'

function buildObjMap(node: TreeNode, map: Map<number, TreeNode>) {
  if (node.obj_num >= 0) map.set(node.obj_num, node)
  for (const child of node.children) buildObjMap(child, map)
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

  const handleFile = useCallback(async (file: File) => {
    setLoading(true)
    setError(null)
    setSelected(null)
    setRootNode(null)
    try {
      const resp = await uploadPdf(file)
      setUploadId(resp.id)
      setFilename(resp.filename)
      setVersion(resp.version)
      setRootNode(resp.tree)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [])

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
            <DetailPane node={selected} uploadId={uploadId} onJumpToObj={handleJumpToObj} />
          </Panel>
        </PanelGroup>
      </div>
    </div>
  )
}

export default App
