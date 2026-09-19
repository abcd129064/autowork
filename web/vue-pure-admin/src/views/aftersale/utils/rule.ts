import { reactive } from "vue";
import type { FormRules } from "element-plus";

/**
 * 售后记录表单校验
 *
 * 只对**业务必填**字段设 required：
 * - 后端 `POST /api/records` 只要求「至少一个可写字段」（app.py:236），
 *   所以前端不该用一堆 required 把用户拦住 —— 这里只锁真正不能空的语义字段。
 */
export const formRules = reactive(<FormRules>{
  problem: [
    { required: true, message: "问题描述为必填项", trigger: "blur" },
    { min: 2, max: 500, message: "长度在 2 到 500 个字符", trigger: "blur" }
  ],
  issue_type: [
    { required: true, message: "请选择问题类型", trigger: "change" }
  ],
  resolved: [
    { required: true, message: "请选择是否解决", trigger: "change" }
  ]
});
