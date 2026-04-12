<!-- frontend/src/components/Dice3D/Dice3D.vue -->
<template>
  <div ref="containerRef" class="dice-3d-container"></div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import * as THREE from 'three'
import * as CANNON from 'cannon-es'

// ==================== 响应式变量声明 ====================
const containerRef = ref<HTMLDivElement | null>(null)

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let diceMesh: THREE.Mesh | null = null
let diceBody: CANNON.Body | null = null
let world: CANNON.World | null = null
let animationId: number | null = null
let isRolling = false
let isDecelerating = false          // 是否正在缓停引导中
let decelerationStartTime = 0
let decelerationDuration = 2000     // 2秒
let startQuat: THREE.Quaternion = new THREE.Quaternion()
let targetQuat: THREE.Quaternion = new THREE.Quaternion()
let resolveRoll: ((value: number) => void) | null = null
// ============================================================
// 后端数据接收：expectedResult 存储父组件传入的预期骰子点数
// 该值通过 throwDice(expectedNumber) 参数接收，来源通常是后端返回的掷骰结果
// ============================================================
let expectedResult: number | null = null

// 存储纹理
let faceTextures: THREE.CanvasTexture[] = []
let currentGlowNumber: number = -1
let glowTimer: ReturnType<typeof setTimeout> | null = null

// 存储每个面的中心点（局部坐标）
let faceCenters: THREE.Vector3[] = []

// ==================== 视觉重心偏移计算 ====================
const getVisualOffset = (num: number): number => {
  const offsets: Record<number, number> = {
    1: 25, 2: 10, 3: 8, 4: 0, 5: 0,
    6: 0, 7: 0, 8: 0, 9: 0, 10: 42,
    11: 42, 12: 42, 13: 43, 14: 42, 15: 42,
    16: 42, 17: 42, 18: 42, 19: 42, 20: 42
  }
  return offsets[num] || 0
}

// ==================== 纹理生成功能 ====================
const createNumberTexture = (num: number, glow: boolean = false): THREE.CanvasTexture => {
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 512
  const ctx = canvas.getContext('2d')!
  
  ctx.fillStyle = '#1a0f0a'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  
  ctx.strokeStyle = '#c4923a'
  ctx.lineWidth = 12
  ctx.strokeRect(20, 20, canvas.width - 40, canvas.height - 40)
  
  ctx.strokeStyle = '#d4a060'
  ctx.lineWidth = 3
  ctx.strokeRect(35, 35, canvas.width - 70, canvas.height - 70)
  
  const offsetY = getVisualOffset(num)
  const centerY = canvas.height / 2 + offsetY
  
  if (glow) {
    ctx.shadowColor = '#ffffff'
    ctx.shadowBlur = 40
    ctx.fillStyle = '#ffffff'
    ctx.font = `bold 200px "Cinzel", "Georgia", serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(num.toString(), canvas.width / 2, centerY)
    
    ctx.shadowBlur = 20
    ctx.fillStyle = '#ffffaa'
    ctx.fillText(num.toString(), canvas.width / 2, centerY)
    
    ctx.shadowBlur = 5
    ctx.fillStyle = '#ffffff'
    ctx.fillText(num.toString(), canvas.width / 2, centerY)
    
    ctx.shadowBlur = 0
  } else {
    ctx.fillStyle = '#ffcc80'
    ctx.font = `bold 200px "Cinzel", "Georgia", serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(num.toString(), canvas.width / 2, centerY)
  }
  
  ctx.fillStyle = '#8a6a4a'
  ctx.font = `20px "Cinzel", serif`
  ctx.fillText('⚔', 50, 60)
  ctx.fillText('⚔', canvas.width - 70, canvas.height - 50)
  
  const texture = new THREE.CanvasTexture(canvas)
  texture.needsUpdate = true
  return texture
}

const createAllTextures = () => {
  const textures: THREE.CanvasTexture[] = []
  for (let num = 1; num <= 20; num++) {
    textures.push(createNumberTexture(num, false))
  }
  return textures
}

// ==================== 数字发光效果 ====================
const glowByNumber = (number: number) => {
  if (!diceMesh || !faceTextures.length) return
  
  if (currentGlowNumber !== -1 && currentGlowNumber !== number) {
    const oldIndex = currentGlowNumber - 1
    const newTexture = createNumberTexture(currentGlowNumber, false)
    faceTextures[oldIndex] = newTexture
    if (diceMesh.material && (diceMesh.material as THREE.Material[])[oldIndex]) {
      ((diceMesh.material as THREE.Material[])[oldIndex] as THREE.MeshStandardMaterial).map = newTexture
      newTexture.needsUpdate = true
    }
  }
  
  const index = number - 1
  const glowTexture = createNumberTexture(number, true)
  faceTextures[index] = glowTexture
  
  if (diceMesh.material && (diceMesh.material as THREE.Material[])[index]) {
    ((diceMesh.material as THREE.Material[])[index] as THREE.MeshStandardMaterial).map = glowTexture
    glowTexture.needsUpdate = true
  }
  
  currentGlowNumber = number
  
  if (glowTimer) clearTimeout(glowTimer)
  glowTimer = setTimeout(() => {
    if (diceMesh && currentGlowNumber !== -1) {
      const idx = currentGlowNumber - 1
      const normalTexture = createNumberTexture(currentGlowNumber, false)
      faceTextures[idx] = normalTexture
      if (diceMesh.material && (diceMesh.material as THREE.Material[])[idx]) {
        ((diceMesh.material as THREE.Material[])[idx] as THREE.MeshStandardMaterial).map = normalTexture
        normalTexture.needsUpdate = true
      }
      currentGlowNumber = -1
    }
  }, 2000)
}

// ==================== UV映射 ====================
const createCustomUVs = (geometry: THREE.BufferGeometry) => {
  const positionAttribute = geometry.attributes.position
  const uvAttribute = new THREE.BufferAttribute(new Float32Array(positionAttribute.count * 2), 2)
  
  for (let i = 0; i < positionAttribute.count; i += 3) {
    const p1 = new THREE.Vector3().fromBufferAttribute(positionAttribute, i)
    const p2 = new THREE.Vector3().fromBufferAttribute(positionAttribute, i + 1)
    const p3 = new THREE.Vector3().fromBufferAttribute(positionAttribute, i + 2)
    
    const center = new THREE.Vector3().add(p1).add(p2).add(p3).multiplyScalar(1/3)
    
    const v1 = new THREE.Vector3().subVectors(p1, center)
    const v2 = new THREE.Vector3().subVectors(p2, center)
    const v3 = new THREE.Vector3().subVectors(p3, center)
    
    const len1 = v1.length()
    const len2 = v2.length()
    const len3 = v3.length()
    
    if (len1 >= len2 && len1 >= len3) {
      uvAttribute.setXY(i, 0.5, 1)
      uvAttribute.setXY(i + 1, 0, 0)
      uvAttribute.setXY(i + 2, 1, 0)
    } else if (len2 >= len1 && len2 >= len3) {
      uvAttribute.setXY(i, 0, 0)
      uvAttribute.setXY(i + 1, 0.5, 1)
      uvAttribute.setXY(i + 2, 1, 0)
    } else {
      uvAttribute.setXY(i, 0, 0)
      uvAttribute.setXY(i + 1, 1, 0)
      uvAttribute.setXY(i + 2, 0.5, 1)
    }
  }
  
  geometry.setAttribute('uv', uvAttribute)
}

// ==================== 计算每个面的中心点 ====================
const computeFaceCenters = (geometry: THREE.BufferGeometry) => {
  const centers: THREE.Vector3[] = []
  const positionAttribute = geometry.attributes.position
  
  for (let i = 0; i < positionAttribute.count; i += 3) {
    const p1 = new THREE.Vector3().fromBufferAttribute(positionAttribute, i)
    const p2 = new THREE.Vector3().fromBufferAttribute(positionAttribute, i + 1)
    const p3 = new THREE.Vector3().fromBufferAttribute(positionAttribute, i + 2)
    
    const center = new THREE.Vector3().add(p1).add(p2).add(p3).multiplyScalar(1/3)
    centers.push(center)
  }
  
  return centers
}

// ==================== 创建骰子 ====================
const createDice = () => {
  const geometry = new THREE.IcosahedronGeometry(1.2, 0)
  createCustomUVs(geometry)
  
  faceCenters = computeFaceCenters(geometry)
  
  faceTextures = createAllTextures()
  
  const materials: THREE.MeshStandardMaterial[] = []
  for (let i = 0; i < 20; i++) {
    const material = new THREE.MeshStandardMaterial({
      map: faceTextures[i],
      color: 0xffffff,
      metalness: 0.2,
      roughness: 0.4,
      emissive: 0x442200,
      emissiveIntensity: 0.2
    })
    materials.push(material)
  }
  
  for (let i = 0; i < 20; i++) {
    geometry.addGroup(i * 3, 3, i)
  }
  
  const mesh = new THREE.Mesh(geometry, materials)
  mesh.castShadow = true
  mesh.receiveShadow = false
  
  const edgesGeo = new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.2, 0))
  const edgesMat = new THREE.LineBasicMaterial({ color: 0xc4923a })
  const wireframe = new THREE.LineSegments(edgesGeo, edgesMat)
  mesh.add(wireframe)
  
  return mesh
}

// ==================== 检测正面数字 ====================
const getFrontNumber = (): number => {
  if (!diceMesh || !camera || faceCenters.length === 0) {
    return Math.floor(Math.random() * 20) + 1
  }
  
  const cameraPos = camera.getWorldPosition(new THREE.Vector3())
  
  let maxDot = -Infinity
  let frontFaceIndex = -1
  
  for (let i = 0; i < faceCenters.length; i++) {
    const worldCenter = faceCenters[i].clone().applyQuaternion(diceMesh.quaternion).add(diceMesh.position)
    const toCamera = new THREE.Vector3().subVectors(cameraPos, worldCenter).normalize()
    const faceNormal = worldCenter.clone().sub(diceMesh.position).normalize()
    const dot = faceNormal.dot(toCamera)
    
    if (dot > maxDot) {
      maxDot = dot
      frontFaceIndex = i
    }
  }
  
  if (maxDot < 0.3) {
    console.warn('没有面正对相机，返回随机数字')
    return Math.floor(Math.random() * 20) + 1
  }
  
  return frontFaceIndex + 1
}

// ==================== 计算目标四元数（使指定面朝向相机） ====================
const getTargetQuaternion = (targetNumber: number): THREE.Quaternion => {
  if (!diceMesh || !camera) return new THREE.Quaternion()
  
  const targetIdx = targetNumber - 1
  const localNormal = faceCenters[targetIdx].clone().normalize()
  const cameraPos = camera.getWorldPosition(new THREE.Vector3())
  const targetDir = cameraPos.clone().sub(diceMesh.position).normalize()
  const worldNormal = localNormal.clone().applyQuaternion(diceMesh.quaternion)
  const quatRot = new THREE.Quaternion().setFromUnitVectors(worldNormal, targetDir)
  return quatRot.clone().multiply(diceMesh.quaternion)
}

// ==================== 缓停引导（2秒内逐渐减速并转向目标面） ====================
// 该函数用于当实际停止面与期望数字不一致时，优雅地修正结果
const startDeceleratingToFace = (targetNumber: number) => {
  if (!diceMesh || !diceBody) return
  
  // 记录起始旋转
  startQuat.copy(diceMesh.quaternion)
  // 计算目标旋转
  targetQuat = getTargetQuaternion(targetNumber)
  decelerationStartTime = performance.now()
  isDecelerating = true
  isRolling = false  // 物理滚动结束标志
  
  // 让物理体逐渐减速
  // 注意：在动画循环中我们会每帧衰减速度，并强制设置旋转
}

// ==================== 物理世界 ====================
const initPhysics = () => {
  world = new CANNON.World()
  world.gravity = new CANNON.Vec3(0, -20, 0)
  
  const groundMaterial = new CANNON.Material('ground')
  const groundBody = new CANNON.Body({
    mass: 0,
    material: groundMaterial,
    shape: new CANNON.Box(new CANNON.Vec3(3.5, 0.2, 3.5)),
    position: new CANNON.Vec3(0, -1.8, 0)
  })
  world.addBody(groundBody)
  
  const wallMaterial = new CANNON.Material('wall')
  const walls = [
    { pos: [3, 0, 0], size: [0.2, 2.5, 3] },
    { pos: [-3, 0, 0], size: [0.2, 2.5, 3] },
    { pos: [0, 0, 3], size: [3, 2.5, 0.2] },
    { pos: [0, 0, -3], size: [3, 2.5, 0.2] }
  ]
  walls.forEach(wall => {
    const wallBody = new CANNON.Body({
      mass: 0,
      material: wallMaterial,
      shape: new CANNON.Box(new CANNON.Vec3(wall.size[0], wall.size[1], wall.size[2])),
      position: new CANNON.Vec3(wall.pos[0], wall.pos[1], wall.pos[2])
    })
    world!.addBody(wallBody)
  })
  
  const diceMaterial = new CANNON.Material('dice')
  const shape = new CANNON.Sphere(1.2)
  diceBody = new CANNON.Body({
    mass: 1,
    material: diceMaterial,
    shape: shape,
    position: new CANNON.Vec3(0, 3.5, 0)
  })
  world.addBody(diceBody)
  
  const contactMaterial = new CANNON.ContactMaterial(diceMaterial, groundMaterial, {
    friction: 0.3,
    restitution: 0.7
  })
  world.addContactMaterial(contactMaterial)
  world.defaultContactMaterial.materials = [diceMaterial, groundMaterial]
}

// ==================== 掷骰子（接受后端点数） ====================
// 后端调用此方法，传入期望的数字（例如后端掷骰计算出的结果）
// 前端会根据该数字播放动画，并最终通过 Promise 返回实际显示的数字
const throwDice = (expectedNumber?: number): Promise<number> => {
  return new Promise((resolve) => {
    if (isRolling || isDecelerating || !diceBody) {
      resolve(0)
      return
    }
    
    // ============================================================
    // 接收后端数据：expectedNumber 来自父组件（通常由后端掷骰结果驱动）
    // 如果后端未提供预期点数（例如独立投掷），则随机生成 1-20
    // ============================================================
    if (expectedNumber === undefined) {
      expectedResult = Math.floor(Math.random() * 20) + 1
    } else {
      // 后端传入的预期点数存储到 expectedResult，用于后续缓停引导
      expectedResult = expectedNumber
    }
    
    isRolling = true
    resolveRoll = resolve
    
    const angleX = (Math.random() - 0.5) * Math.PI * 1.2
    const angleZ = (Math.random() - 0.5) * Math.PI * 1.2
    
    diceBody.applyForce(
      new CANNON.Vec3(Math.sin(angleX) * 15, 18, Math.sin(angleZ) * 15),
      diceBody.position
    )
    
    diceBody.angularVelocity.set(
      (Math.random() - 0.5) * 25,
      (Math.random() - 0.5) * 25,
      (Math.random() - 0.5) * 25
    )
    
    diceBody.position.set(0, 3.5, 0)
    diceBody.velocity.set(0, 0, 0)
    diceBody.wakeUp()
  })
}

// ==================== 停止检测与缓停引导 ====================
const checkStopped = () => {
  if (!isRolling || !diceBody) return
  
  const speed = diceBody.velocity.length()
  const angularSpeed = diceBody.angularVelocity.length()
  const yPos = diceBody.position.y
  
  // 当速度足够低且高度接近地面时，启动缓停引导
  if (speed < 1.0 && angularSpeed < 1.0 && yPos < 0.8) {
    const actualNumber = getFrontNumber()
    const targetNumber = expectedResult
    // ============================================================
    // 后端数据对比：如果实际停止面与后端期望结果不符，
    // 则启动 2 秒缓停动画，强制将骰子旋转到期望数字面朝相机。
    // 这保证了前端展示结果与后端计算结果一致。
    // ============================================================
    if (targetNumber !== null && actualNumber !== targetNumber) {
      console.log(`实际正面: ${actualNumber}, 期望: ${targetNumber}, 开始2秒缓停引导`)
      startDeceleratingToFace(targetNumber)
    } else {
      // 如果已经正确，直接结束并返回结果
      isRolling = false
      const finalNumber = actualNumber
      console.log(`最终正面数字: ${finalNumber}`)
      glowByNumber(finalNumber)
      if (resolveRoll) {
        // 将最终数字返回给调用方（父组件）
        resolveRoll(finalNumber)
        resolveRoll = null
      }
      expectedResult = null
    }
  }
}

// ==================== 3D场景 ====================
const initScene = () => {
  if (!containerRef.value) return
  
  const width = window.innerWidth
  const height = window.innerHeight
  
  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x050308)
  scene.fog = new THREE.FogExp2(0x050308, 0.008)
  
  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000)
  camera.position.set(5.5, 2.5, 7)
  camera.lookAt(0, 0, 0)
  
  renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setSize(width, height)
  renderer.setPixelRatio(window.devicePixelRatio)
  renderer.shadowMap.enabled = true
  containerRef.value.appendChild(renderer.domElement)
  
  const ambientLight = new THREE.AmbientLight(0x404040, 0.6)
  scene.add(ambientLight)
  
  const mainLight = new THREE.DirectionalLight(0xffeedd, 1.5)
  mainLight.position.set(2, 5, 3)
  mainLight.castShadow = true
  scene.add(mainLight)
  
  const backLight = new THREE.DirectionalLight(0xccaa88, 0.8)
  backLight.position.set(-2, 2, -4)
  scene.add(backLight)
  
  const fillLight = new THREE.PointLight(0xaa8866, 0.5)
  fillLight.position.set(1, 2, 2)
  scene.add(fillLight)
  
  const rimLight = new THREE.PointLight(0xc4923a, 0.5)
  rimLight.position.set(0, 1, -4.5)
  scene.add(rimLight)
  
  diceMesh = createDice()
  scene.add(diceMesh)
}

// ==================== 动画循环 ====================
const animate = () => {
  const now = performance.now()
  
  if (world) {
    // 如果处于缓停引导模式，每帧手动控制旋转和衰减速度
    if (isDecelerating && diceMesh && diceBody) {
      const elapsed = now - decelerationStartTime
      let t = Math.min(1, elapsed / decelerationDuration)
      // 使用 easeOutCubic 缓动，让减速更自然
      t = 1 - Math.pow(1 - t, 3)
      
      // 四元数插值
      const newQuat = startQuat.clone().slerp(targetQuat, t)
      diceMesh.quaternion.copy(newQuat)
      
      // 同步到物理体
      const cannonQuat = new CANNON.Quaternion(newQuat.x, newQuat.y, newQuat.z, newQuat.w)
      diceBody.quaternion.copy(cannonQuat)
      
      // 逐渐衰减线速度和角速度，模拟减速
      diceBody.velocity.scale(0.95, diceBody.velocity)
      diceBody.angularVelocity.scale(0.95, diceBody.angularVelocity)
      
      // 如果插值完成，结束缓停
      if (t >= 1.0) {
        isDecelerating = false
        // 完全停止物理运动
        diceBody.angularVelocity.set(0, 0, 0)
        diceBody.velocity.set(0, 0, 0)
        diceBody.sleep()
        
        const finalNumber = getFrontNumber()
        console.log(`缓停结束，最终正面数字: ${finalNumber}`)
        glowByNumber(finalNumber)
        if (resolveRoll) {
          // 返回最终数字给后端调用方
          resolveRoll(finalNumber)
          resolveRoll = null
        }
        expectedResult = null
      }
    } else {
      // 正常物理步进
      world.step(1 / 60)
      
      if (diceMesh && diceBody) {
        diceMesh.position.copy(diceBody.position as any)
        diceMesh.quaternion.copy(diceBody.quaternion as any)
      }
      
      checkStopped()
    }
  }
  
  if (renderer && scene && camera) {
    renderer.render(scene, camera)
  }
  
  animationId = requestAnimationFrame(animate)
}

// ==================== 窗口适配 ====================
const handleResize = () => {
  if (!containerRef.value || !renderer || !camera) return
  const width = window.innerWidth
  const height = window.innerHeight
  camera.aspect = width / height
  camera.updateProjectionMatrix()
  renderer.setSize(width, height)
}

// ==================== 清理资源 ====================
const cleanup = () => {
  if (animationId) {
    cancelAnimationFrame(animationId)
    animationId = null
  }
  
  if (glowTimer) {
    clearTimeout(glowTimer)
    glowTimer = null
  }
  
  window.removeEventListener('resize', handleResize)
  
  if (world) {
    world.bodies.forEach(body => {
      world!.removeBody(body)
    })
    world.contactmaterials.forEach((cm: CANNON.ContactMaterial) => {
      world!.removeContactMaterial(cm)
    })
    world = null
  }
  
  if (diceMesh) {
    diceMesh.geometry.dispose()
    if (Array.isArray(diceMesh.material)) {
      diceMesh.material.forEach((material: THREE.Material) => material.dispose())
    } else if (diceMesh.material) {
      diceMesh.material.dispose()
    }
    diceMesh = null
  }
  
  if (scene) {
    scene.traverse((object: THREE.Object3D) => {
      if (object instanceof THREE.Mesh) {
        object.geometry.dispose()
        if (object.material) {
          if (Array.isArray(object.material)) {
            object.material.forEach((material: THREE.Material) => material.dispose())
          } else {
            object.material.dispose()
          }
        }
      }
    })
    scene.clear()
    scene = null
  }
  
  if (renderer) {
    renderer.dispose()
    if (renderer.domElement && renderer.domElement.parentNode) {
      renderer.domElement.parentNode.removeChild(renderer.domElement)
    }
    renderer = null
  }
  
  if (containerRef.value) {
    containerRef.value.innerHTML = ''
  }
  
  camera = null
  diceBody = null
  resolveRoll = null
  expectedResult = null
  isDecelerating = false
}

// ==================== 暴露接口 ====================
// 父组件可通过 ref 调用 throwDice(expectedNumber) 方法，
// 其中 expectedNumber 通常由后端掷骰计算得出
defineExpose({
  throwDice
})

// ==================== 生命周期 ====================
onMounted(() => {
  initScene()
  initPhysics()
  animate()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  cleanup()
})
</script>

<style scoped>
.dice-3d-container {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #050308;
  pointer-events: none;
}

.dice-3d-container canvas {
  pointer-events: auto;
}
</style>