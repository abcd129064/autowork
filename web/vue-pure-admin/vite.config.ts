import { getPluginsList } from "./build/plugins.ts";
import { include, exclude } from "./build/optimize.ts";
import { type UserConfigExport, type ConfigEnv, loadEnv } from "vite";
import {
  root,
  alias,
  wrapperEnv,
  pathResolve,
  __APP_INFO__
} from "./build/utils.ts";

export default async ({ mode }: ConfigEnv): Promise<UserConfigExport> => {
  const {
    VITE_CDN,
    VITE_PORT,
    VITE_API_TARGET,
    VITE_COMPRESSION,
    VITE_PUBLIC_PATH
  } = wrapperEnv(loadEnv(mode, root));
  return {
    base: VITE_PUBLIC_PATH,
    root,
    resolve: {
      alias
    },
    // 服务端渲染
    server: {
      // 端口号
      port: VITE_PORT,
      host: "0.0.0.0",
      // 本地跨域代理 https://cn.vitejs.dev/config/server-options.html#server-proxy
      proxy: {
        // autowork 售后接口（生产 nginx 80 端口，路径前缀 /api）
        // 注意：vue-pure-admin 自带 mock 也占用 /api，这里按前缀精确匹配，
        // 仅代理售后相关路径，避免打穿平台的 mock 路由
        "/api/health": { target: VITE_API_TARGET, changeOrigin: true },
        "/api/cycle-options": { target: VITE_API_TARGET, changeOrigin: true },
        "/api/records": { target: VITE_API_TARGET, changeOrigin: true },
        "/api/table-columns": { target: VITE_API_TARGET, changeOrigin: true },
        "/api/stats": { target: VITE_API_TARGET, changeOrigin: true },
        "/api/auth": { target: VITE_API_TARGET, changeOrigin: true }
      },
      // 预热文件以提前转换和缓存结果，降低启动期间的初始页面加载时长并防止转换瀑布
      warmup: {
        clientFiles: ["./index.html", "./src/{views,components}/*"]
      }
    },
    plugins: await getPluginsList(VITE_CDN, VITE_COMPRESSION),
    // https://cn.vitejs.dev/config/dep-optimization-options.html#dep-optimization-options
    optimizeDeps: {
      include,
      exclude
    },
    build: {
      // https://cn.vitejs.dev/guide/build.html#browser-compatibility
      target: "es2015",
      sourcemap: false,
      // 消除打包大小超过500kb警告
      chunkSizeWarningLimit: 4000,
      rolldownOptions: {
        input: {
          index: pathResolve("./index.html", import.meta.url)
        },
        // 静态资源分类打包
        output: {
          chunkFileNames: "static/js/[name]-[hash].js",
          entryFileNames: "static/js/[name]-[hash].js",
          assetFileNames: "static/[ext]/[name]-[hash].[ext]"
        },
        checks: {
          pluginTimings: false,
          toleratedTransform: false
        }
      }
    },
    define: {
      __INTLIFY_PROD_DEVTOOLS__: false,
      __APP_INFO__: JSON.stringify(__APP_INFO__)
    }
  };
};
