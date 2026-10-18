<script setup>
import { modal, hideModal, confirmModal } from '../modal'

/**
 * 确认弹窗。
 *
 * `#modal-overlay` 用 v-show 而不是 v-if：E2E 有一条用例要点在遮罩本身的
 * 左上角 (`position={'x':10,'y':10}`) 来关窗，节点不存在就点不着；用
 * v-show 时隐藏态是 `display:none`，`is_hidden()` 同样返回 True。
 *
 * 点击关闭的判断条件是 `event.target === event.currentTarget`——只有点在
 * 遮罩自身才关，点在白色卡片上不关。旧版用的是同一个判断。
 */
function handleOverlayClick(event) {
  if (event.target === event.currentTarget) {
    hideModal()
  }
}
</script>

<template>
  <div
    class="modal-overlay"
    id="modal-overlay"
    v-show="modal.visible"
    @click="handleOverlayClick"
  >
    <div class="modal">
      <div class="modal-header">
        <h3 id="modal-title">{{ modal.title }}</h3>
      </div>
      <div class="modal-body">
        <p id="modal-message">{{ modal.message }}</p>
      </div>
      <div class="modal-actions">
        <button class="btn btn-secondary" id="modal-cancel" @click="hideModal">取消</button>
        <button class="btn btn-danger" id="modal-confirm" @click="confirmModal">确认</button>
      </div>
    </div>
  </div>
</template>
