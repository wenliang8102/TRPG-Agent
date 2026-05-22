<!-- frontend/src/components/Dice3D/Dice3D.vue -->
<template>
  <div ref="containerRef" class="dice-3d-container" :class="{ embedded }"></div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import * as THREE from 'three'
import * as CANNON from 'cannon-es'

const props = withDefaults(defineProps<{
  embedded?: boolean
}>(), {
  embedded: false,
})

// ==================== 响应式变量 ====================
const containerRef = ref<HTMLDivElement | null>(null)

let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let renderer: THREE.WebGLRenderer | null = null
let diceMesh: THREE.Mesh | null = null
let diceBody: CANNON.Body | null = null
let world: CANNON.World | null = null
let animationId: number | null = null
let scriptedAnimationId: number | null = null
let isRolling = false
let isDecelerating = false
let isScriptedRolling = false
let isDisplayLocked = false
let decelerationStartTime = 0
let decelerationDuration = 3000 // 最大引导时长3秒
let resolveRoll: ((value: number) => void) | null = null
let expectedResult: number | null = null

// 扭矩引导参数（轻柔）
const kpGentle = 5.0
const kdStrong = 8.0
const maxTorqueGentle = 15.0
let targetQuatCannon: CANNON.Quaternion | null = null

// 低高度持续时间检测
let lowHeightStartTime: number | null = null
const LOW_HEIGHT_THRESHOLD = 0.3
const LOW_HEIGHT_DURATION = 1530 // 毫秒

// 纹理与发光
let faceTextures: THREE.CanvasTexture[] = []
let currentGlowNumber: number = -1
let glowTimer: ReturnType<typeof setTimeout> | null = null
let faceCenters: THREE.Vector3[] = []

const getDiceRadius = () => props.embedded ? 1.65 : 1.2

// ==================== 视觉偏移 ====================
const getVisualOffset = (num: number): number => {
  const offsets: Record<number, number> = {
    1: 25, 2: 10, 3: 8, 4: 0, 5: 0,
    6: 0, 7: 0, 8: 0, 9: 0, 10: 42,
    11: 42, 12: 42, 13: 43, 14: 42, 15: 42,
    16: 42, 17: 42, 18: 42, 19: 42, 20: 42
  }
  return offsets[num] || 0
}

// ==================== 纹理生成 ====================
const createNumberTexture = (num: number, glow: boolean = false): THREE.CanvasTexture => {
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 512
  const ctx = canvas.getContext('2d')!

  const gradient = ctx.createLinearGradient(96, 32, 416, 480)
  gradient.addColorStop(0, '#263fd8')
  gradient.addColorStop(0.48, '#1020a8')
  gradient.addColorStop(1, '#07125c')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, canvas.width, canvas.height)

  // 用金线三角结构强化 D20 面片的仪式感，避免依赖额外贴图资源。
  const outerTriangle = new Path2D()
  outerTriangle.moveTo(256, 46)
  outerTriangle.lineTo(442, 424)
  outerTriangle.lineTo(70, 424)
  outerTriangle.closePath()

  const innerTriangle = new Path2D()
  innerTriangle.moveTo(256, 118)
  innerTriangle.lineTo(370, 356)
  innerTriangle.lineTo(142, 356)
  innerTriangle.closePath()

  ctx.strokeStyle = '#d9b85f'
  ctx.lineWidth = 16
  ctx.lineJoin = 'round'
  ctx.stroke(outerTriangle)

  ctx.strokeStyle = '#f0cf73'
  ctx.lineWidth = 5
  ctx.stroke(innerTriangle)

  ctx.strokeStyle = 'rgba(217, 184, 95, 0.75)'
  ctx.lineWidth = 4
  ctx.beginPath()
  ctx.moveTo(256, 46)
  ctx.lineTo(142, 356)
  ctx.moveTo(256, 46)
  ctx.lineTo(370, 356)
  ctx.moveTo(70, 424)
  ctx.lineTo(370, 356)
  ctx.moveTo(442, 424)
  ctx.lineTo(142, 356)
  ctx.stroke()

  ctx.strokeStyle = 'rgba(246, 213, 117, 0.55)'
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.moveTo(112, 156)
  ctx.lineTo(212, 92)
  ctx.moveTo(400, 156)
  ctx.lineTo(300, 92)
  ctx.moveTo(138, 402)
  ctx.lineTo(220, 426)
  ctx.moveTo(374, 402)
  ctx.lineTo(292, 426)
  ctx.stroke()

  const offsetY = getVisualOffset(num)
  const centerY = canvas.height / 2 + offsetY
  
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.font = `bold 190px "Cinzel", "Georgia", serif`
  if (glow) {
    ctx.shadowColor = '#70a7ff'
    ctx.shadowBlur = 46
    ctx.fillStyle = '#fff5c4'
    ctx.fillText(num.toString(), canvas.width / 2, centerY)
  } else {
    ctx.shadowColor = 'rgba(4, 10, 45, 0.85)'
    ctx.shadowBlur = 12
    ctx.fillStyle = '#ffd84f'
    ctx.fillText(num.toString(), canvas.width / 2, centerY)
  }

  ctx.shadowBlur = 0
  
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

// ==================== 数字发光 ====================
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
  const diceRadius = getDiceRadius()
  const geometry = new THREE.IcosahedronGeometry(diceRadius, 0)
  createCustomUVs(geometry)
  
  faceCenters = computeFaceCenters(geometry)
  
  faceTextures = createAllTextures()
  
  const materials: THREE.MeshStandardMaterial[] = []
  for (let i = 0; i < 20; i++) {
    const material = new THREE.MeshStandardMaterial({
      map: faceTextures[i],
      color: 0xffffff,
      metalness: 0.42,
      roughness: 0.28,
      emissive: 0x071f88,
      emissiveIntensity: 0.18
    })
    materials.push(material)
  }
  
  for (let i = 0; i < 20; i++) {
    geometry.addGroup(i * 3, 3, i)
  }
  
  const mesh = new THREE.Mesh(geometry, materials)
  mesh.castShadow = true
  mesh.receiveShadow = false
  
  const edgesGeo = new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(diceRadius, 0))
  const edgesMat = new THREE.LineBasicMaterial({ color: 0xf0cf73 })
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

// ==================== 计算目标四元数 ====================
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

// ==================== 缓停引导 ====================
const startDeceleratingToFace = (targetNumber: number) => {
  if (!diceMesh || !diceBody) return
  
  const targetThreeQuat = getTargetQuaternion(targetNumber)
  targetQuatCannon = new CANNON.Quaternion(
    targetThreeQuat.x,
    targetThreeQuat.y,
    targetThreeQuat.z,
    targetThreeQuat.w
  )
  
  decelerationStartTime = performance.now()
  isDecelerating = true
  isRolling = false
  lowHeightStartTime = null // 重置计时
  
  diceBody.velocity.scale(0.7, diceBody.velocity)
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

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)
const easeInOutCubic = (t: number) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2

// 卡片里的骰子不需要完整物理落地，直接用短动画把目标面锁到正面。
const playScriptedRoll = (targetNumber: number): Promise<number> => {
  return new Promise((resolve) => {
    if (!diceMesh || !camera) {
      resolve(targetNumber)
      return
    }

    if (scriptedAnimationId) {
      cancelAnimationFrame(scriptedAnimationId)
      scriptedAnimationId = null
    }

    isRolling = false
    isDecelerating = false
    isScriptedRolling = true
    isDisplayLocked = false
    targetQuatCannon = null
    expectedResult = targetNumber

    diceMesh.position.set(-2.2, 1.8, 0)
    diceMesh.rotation.set(
      Math.random() * Math.PI,
      Math.random() * Math.PI,
      Math.random() * Math.PI
    )

    const startTime = performance.now()
    const rollDuration = 1700
    const startQuat = diceMesh.quaternion.clone()
    const spinAxis = new THREE.Vector3(0.55, 1, 0.32).normalize()
    const spinQuat = new THREE.Quaternion().setFromAxisAngle(spinAxis, Math.PI * 7.6)
    const targetQuat = getTargetQuaternion(targetNumber)
    const preTargetQuat = spinQuat.clone().multiply(startQuat)

    const tick = () => {
      if (!diceMesh || !camera) {
        isScriptedRolling = false
        resolve(targetNumber)
        return
      }

      const elapsed = performance.now() - startTime
      const progress = Math.min(elapsed / rollDuration, 1)
      const travel = easeOutCubic(progress)
      const settle = easeInOutCubic(Math.max(0, (progress - 0.45) / 0.55))
      const travelY = 1.8 - travel * 1.8 + Math.sin(travel * Math.PI) * 0.52

      diceMesh.position.set(-2.2 + travel * 2.2, travelY, 0)
      diceMesh.quaternion
        .copy(startQuat)
        .slerp(preTargetQuat, Math.min(progress / 0.55, 1))
        .slerp(targetQuat, settle)

      if (progress < 1) {
        scriptedAnimationId = requestAnimationFrame(tick)
        return
      }

      diceMesh.position.set(0, 0, 0)
      diceMesh.quaternion.copy(targetQuat)
      isScriptedRolling = false
      isDisplayLocked = true
      scriptedAnimationId = null
      glowByNumber(targetNumber)
      resolve(targetNumber)
    }

    tick()
  })
}

// ==================== 掷骰子 ====================
const throwDice = (expectedNumber?: number): Promise<number> => {
  return new Promise((resolve) => {
    if (isRolling || isDecelerating || !diceBody) {
      resolve(0)
      return
    }
    
    if (expectedNumber === undefined) {
      expectedResult = Math.floor(Math.random() * 20) + 1
    } else {
      expectedResult = expectedNumber
    }

    if (props.embedded) {
      playScriptedRoll(expectedResult).then(resolve)
      return
    }
    
    isRolling = true
    resolveRoll = resolve
    targetQuatCannon = null
    lowHeightStartTime = null
    
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

// ==================== 停止检测（带持续低高度判断） ====================
const checkStopped = () => {
  if (!isRolling || !diceBody) return
  
  const now = performance.now()
  const speed = diceBody.velocity.length()
  const angularSpeed = diceBody.angularVelocity.length()
  const yPos = diceBody.position.y
  
  // 检查是否低于高度阈值
  if (yPos < LOW_HEIGHT_THRESHOLD) {
    if (lowHeightStartTime === null) {
      lowHeightStartTime = now
    }
    
    const lowHeightElapsed = now - lowHeightStartTime
    // 速度条件满足且低高度持续足够时间，启动引导
    if (speed < 1.5 && angularSpeed < 2.0 && lowHeightElapsed >= LOW_HEIGHT_DURATION) {
      const actualNumber = getFrontNumber()
      const targetNumber = expectedResult
      if (targetNumber !== null && actualNumber !== targetNumber) {
        console.log(`实际: ${actualNumber}, 期望: ${targetNumber}, 启动温柔扭矩引导`)
        startDeceleratingToFace(targetNumber)
      } else {
        isRolling = false
        lowHeightStartTime = null
        const finalNumber = actualNumber
        console.log(`最终: ${finalNumber}`)
        glowByNumber(finalNumber)
        if (resolveRoll) {
          resolveRoll(finalNumber)
          resolveRoll = null
        }
        expectedResult = null
      }
    }
  } else {
    // 高度回升，重置计时
    lowHeightStartTime = null
  }
}

// ==================== 3D场景 ====================
const initScene = () => {
  if (!containerRef.value) return
  
  const { width, height } = getViewportSize()
  
  scene = new THREE.Scene()
  scene.background = null
  
  camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000)
  if (props.embedded) {
    camera.position.set(0, 0.25, 5.6)
  } else {
    camera.position.set(4.7, 2.2, 6.2)
  }
  camera.lookAt(0, 0, 0)
  
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setClearColor(0x000000, 0)
  renderer.setSize(width, height)
  renderer.setPixelRatio(window.devicePixelRatio)
  renderer.shadowMap.enabled = true
  containerRef.value.appendChild(renderer.domElement)
  
  const ambientLight = new THREE.AmbientLight(0x5969aa, 0.72)
  scene.add(ambientLight)
  
  const mainLight = new THREE.DirectionalLight(0xffedb0, 1.75)
  mainLight.position.set(2, 5, 3)
  mainLight.castShadow = true
  scene.add(mainLight)
  
  const backLight = new THREE.DirectionalLight(0x7091ff, 0.75)
  backLight.position.set(-2, 2, -4)
  scene.add(backLight)
  
  const fillLight = new THREE.PointLight(0x456dff, 0.52)
  fillLight.position.set(1, 2, 2)
  scene.add(fillLight)
  
  const rimLight = new THREE.PointLight(0xf0cf73, 0.8)
  rimLight.position.set(0, 1, -4.5)
  scene.add(rimLight)
  
  diceMesh = createDice()
  scene.add(diceMesh)
}

// ==================== 动画循环 ====================
const animate = () => {
  const now = performance.now()
  
  if (isScriptedRolling) {
    if (renderer && scene && camera) {
      renderer.render(scene, camera)
    }
    animationId = requestAnimationFrame(animate)
    return
  }

  if (world && diceBody && diceMesh && !isDisplayLocked) {
    if (isDecelerating && targetQuatCannon) {
      // 扭矩引导模式
      const qCurrent = diceBody.quaternion
      const qTarget = targetQuatCannon
      
      const qError = qTarget.mult(qCurrent.conjugate())
      let angle = 2 * Math.acos(Math.max(-1, Math.min(1, qError.w)))
      const axis = new CANNON.Vec3(qError.x, qError.y, qError.z)
      const len = axis.length()
      if (len > 1e-6) {
        axis.scale(1 / len, axis)
      } else {
        axis.set(0, 1, 0)
      }
      
      const speedFactor = Math.max(0.3, 1.0 - diceBody.velocity.length() / 5.0)
      const angSpeedFactor = Math.max(0.3, 1.0 - diceBody.angularVelocity.length() / 8.0)
      const dynamicGain = kpGentle * speedFactor * angSpeedFactor
      
      const torque = axis.clone().scale(dynamicGain * angle)
      const dampingTorque = diceBody.angularVelocity.clone().scale(-kdStrong)
      torque.vadd(dampingTorque, torque)
      
      const torqueMag = torque.length()
      if (torqueMag > maxTorqueGentle) {
        torque.normalize()
        torque.scale(maxTorqueGentle, torque)
      }
      
      diceBody.torque.copy(torque)
      diceBody.velocity.scale(0.95, diceBody.velocity)
      diceBody.angularVelocity.scale(0.96, diceBody.angularVelocity)
      
      world.step(1 / 60)
      
      diceMesh.position.copy(diceBody.position as any)
      diceMesh.quaternion.copy(diceBody.quaternion as any)
      
      const angleDeg = Math.abs(angle) * (180 / Math.PI)
      const angVel = diceBody.angularVelocity.length()
      const elapsed = now - decelerationStartTime
      
      if ((angleDeg < 3.0 && angVel < 0.5) || elapsed > decelerationDuration) {
        isDecelerating = false
        diceBody.angularVelocity.set(0, 0, 0)
        diceBody.velocity.set(0, 0, 0)
        diceBody.sleep()
        targetQuatCannon = null
        
        const finalNumber = getFrontNumber()
        console.log(`引导完成，最终: ${finalNumber}`)
        glowByNumber(finalNumber)
        if (resolveRoll) {
          resolveRoll(finalNumber)
          resolveRoll = null
        }
        expectedResult = null
      }
    } else {
      // 正常物理模拟
      world.step(1 / 60)
      
      diceMesh.position.copy(diceBody.position as any)
      diceMesh.quaternion.copy(diceBody.quaternion as any)
      
      checkStopped()
    }
  }
  
  if (renderer && scene && camera) {
    renderer.render(scene, camera)
  }
  
  animationId = requestAnimationFrame(animate)
}

// ==================== 窗口适配 ====================
const getViewportSize = () => {
  if (props.embedded && containerRef.value) {
    const rect = containerRef.value.getBoundingClientRect()
    return {
      width: Math.max(1, rect.width),
      height: Math.max(1, rect.height),
    }
  }
  return {
    width: window.innerWidth,
    height: window.innerHeight,
  }
}

const handleResize = () => {
  if (!containerRef.value || !renderer || !camera) return
  const { width, height } = getViewportSize()
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
  if (scriptedAnimationId) {
    cancelAnimationFrame(scriptedAnimationId)
    scriptedAnimationId = null
  }
  if (glowTimer) {
    clearTimeout(glowTimer)
    glowTimer = null
  }
  window.removeEventListener('resize', handleResize)
  
  if (world) {
    world.bodies.forEach(body => world!.removeBody(body))
    world.contactmaterials.forEach((cm: CANNON.ContactMaterial) => world!.removeContactMaterial(cm))
    world = null
  }
  if (diceMesh) {
    diceMesh.geometry.dispose()
    if (Array.isArray(diceMesh.material)) {
      diceMesh.material.forEach(m => m.dispose())
    } else if (diceMesh.material) {
      diceMesh.material.dispose()
    }
    diceMesh = null
  }
  if (scene) {
    scene.traverse(obj => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry.dispose()
        if (Array.isArray(obj.material)) {
          obj.material.forEach(m => m.dispose())
        } else if (obj.material) {
          obj.material.dispose()
        }
      }
    })
    scene.clear()
    scene = null
  }
  if (renderer) {
    renderer.dispose()
    if (renderer.domElement.parentNode) {
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
  isRolling = false
  isScriptedRolling = false
  isDisplayLocked = false
  targetQuatCannon = null
  lowHeightStartTime = null
}

defineExpose({ throwDice })

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
  background: transparent;
  pointer-events: none;
  z-index: 9999;
}

.dice-3d-container.embedded {
  position: absolute;
  width: 100%;
  height: 100%;
  inset: 0;
  z-index: 1;
}

.dice-3d-container canvas {
  pointer-events: auto;
}
</style>
