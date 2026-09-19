import { defineStore } from "pinia";
import {
  type userType,
  store,
  router,
  resetRouter,
  routerArrays,
  storageLocal
} from "../utils";
import {
  type UserResult,
  type RefreshTokenResult,
  authLogin,
  refreshTokenApi
} from "@/api/user";
import { useMultiTagsStoreHook } from "./multiTags";
import { type DataInfo, setToken, removeToken, userKey } from "@/utils/auth";

export const useUserStore = defineStore("pure-user", {
  state: (): userType => ({
    // 头像
    avatar: storageLocal().getItem<DataInfo<number>>(userKey)?.avatar ?? "",
    // 用户名
    username: storageLocal().getItem<DataInfo<number>>(userKey)?.username ?? "",
    // 昵称
    nickname: storageLocal().getItem<DataInfo<number>>(userKey)?.nickname ?? "",
    // 页面级别权限
    roles: storageLocal().getItem<DataInfo<number>>(userKey)?.roles ?? [],
    // 按钮级别权限
    permissions:
      storageLocal().getItem<DataInfo<number>>(userKey)?.permissions ?? [],
    // 前端生成的验证码（按实际需求替换）
    verifyCode: "",
    // 判断登录页面显示哪个组件（0：登录（默认）、1：手机登录、2：二维码登录、3：注册、4：忘记密码）
    currentPage: 0,
    // 是否勾选了登录页的免登录
    isRemembered: false,
    // 登录页的免登录存储几天，默认7天
    loginDay: 7
  }),
  actions: {
    /** 存储头像 */
    SET_AVATAR(avatar: string) {
      this.avatar = avatar;
    },
    /** 存储用户名 */
    SET_USERNAME(username: string) {
      this.username = username;
    },
    /** 存储昵称 */
    SET_NICKNAME(nickname: string) {
      this.nickname = nickname;
    },
    /** 存储角色 */
    SET_ROLES(roles: Array<string>) {
      this.roles = roles;
    },
    /** 存储按钮级别权限 */
    SET_PERMS(permissions: Array<string>) {
      this.permissions = permissions;
    },
    /** 存储前端生成的验证码 */
    SET_VERIFYCODE(verifyCode: string) {
      this.verifyCode = verifyCode;
    },
    /** 存储登录页面显示哪个组件 */
    SET_CURRENTPAGE(value: number) {
      this.currentPage = value;
    },
    /** 存储是否勾选了登录页的免登录 */
    SET_ISREMEMBERED(bool: boolean) {
      this.isRemembered = bool;
    },
    /** 设置登录页的免登录存储几天 */
    SET_LOGINDAY(value: number) {
      this.loginDay = Number(value);
    },
    /**
     * 登入（走售后后端真实 JWT）
     *
     * 后端 `/api/auth/login` 返回扁平 `{ token, user }`（401=凭据错误），
     * 这里包装成模板统一的 `{code,data}` 结构并写入 token 存储。
     * - roles/permissions 给 admin + `*:*:*`：本系统路由是静态的，仅按钮权限用到
     * - expires 取 11.5h：后端 JWT 是 12h，留半小时余量，
     *   避免「前端还没判定过期、后端已拒绝」的边缘 401
     * - 后端没有 refresh-token 接口，refreshToken 置空串；
     *   过期后由 PureHttp 触发 mock 刷新 → 写请求 401 → 提示重新登录
     */
    async loginByUsername(data) {
      return new Promise<UserResult>((resolve, reject) => {
        authLogin({
          username: data.username,
          password: data.password
        })
          .then(res => {
            if (res?.token) {
              const wrapped: UserResult = {
                code: 0,
                message: "登录成功",
                data: {
                  avatar: "",
                  username: res.user,
                  nickname: res.user,
                  roles: ["admin"],
                  permissions: ["*:*:*"],
                  accessToken: res.token,
                  refreshToken: "",
                  expires: new Date(
                    Date.now() + 11.5 * 3600 * 1000
                  ) as unknown as Date
                }
              };
              setToken(wrapped.data);
              resolve(wrapped);
            } else {
              reject("登录响应缺少 token");
            }
          })
          .catch(error => {
            reject(error);
          });
      });
    },
    /** 前端登出（不调用接口） */
    logOut() {
      this.username = "";
      this.roles = [];
      this.permissions = [];
      removeToken();
      useMultiTagsStoreHook().handleTags("equal", [...routerArrays]);
      resetRouter();
      router.push("/login");
    },
    /** 刷新`token` */
    async handRefreshToken(data) {
      return new Promise<RefreshTokenResult>((resolve, reject) => {
        refreshTokenApi(data)
          .then(data => {
            if (data.code === 0) {
              setToken(data.data);
              resolve(data);
            } else {
              reject(data.message);
            }
          })
          .catch(error => {
            reject(error);
          });
      });
    }
  }
});

export function useUserStoreHook() {
  return useUserStore(store);
}
