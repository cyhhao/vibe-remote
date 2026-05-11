# 用 Incus 创建 Vibe Remote 租户

这个脚手架用于在一台 Incus 宿主机上创建多个 Vibe Remote 租户。它适合小范围托管 Bot、内部试点，以及在完整 avibe.bot 控制面完成前先手动运营。

可信试点用户可以先用 container。用户不够可信，或者需要更强隔离时，用 `--type vm`。

## 初始化宿主机

先在宿主机安装 Incus，然后运行：

```bash
python3 scripts/incus_tenant.py init-host --minimal
```

`--minimal` 会执行 `incus admin init --minimal`。如果宿主机已经初始化过，可以不加。脚本默认使用 storage pool `default` 和网络桥 `incusbr0`；如果你的宿主机名字不同，在创建租户时用参数覆盖。

只检查、不修改宿主机：

```bash
python3 scripts/incus_tenant.py doctor
```

## 创建租户

```bash
python3 scripts/incus_tenant.py create alice \
  --cpus 2 \
  --memory 4GiB \
  --disk 30GiB \
  --processes 4096 \
  --backend codex \
  --ui-host-port 15123
```

它会创建：

- Incus project：`vr-alice`
- 实例名：`vibe`
- 实例内 Linux 用户：`vibe`
- 工作目录：`/home/vibe/work`
- 实例内 Vibe Remote Web UI：`5123`
- 可选宿主机 Web UI 代理：`http://127.0.0.1:15123`

首次启动后等待 cloud-init 完成：

```bash
python3 scripts/incus_tenant.py wait-ready alice
```

然后打开 `create` 输出的 Web UI 地址，按正常 Vibe Remote 向导给这个租户配置 Slack、Discord、Telegram、Lark 或 WeChat。

## 启停和运维

```bash
python3 scripts/incus_tenant.py status alice
python3 scripts/incus_tenant.py shell alice
python3 scripts/incus_tenant.py exec alice -- pwd
python3 scripts/incus_tenant.py stop alice
python3 scripts/incus_tenant.py start alice
python3 scripts/incus_tenant.py restart alice
python3 scripts/incus_tenant.py list
```

删除租户和所有租户数据：

```bash
python3 scripts/incus_tenant.py delete alice
```

任何命令都可以加 `--dry-run`，只打印将要执行的 Incus 命令，不真正执行。

Web UI 代理默认只监听 `127.0.0.1`。如果要绑定到其他宿主机地址，需要显式传
`--ui-host <address>`，并放在防火墙或反向代理后面。

## 资源分配

脚手架会同时在 Incus project 和该 project 的 `default` profile 上设置资源限制：

- `limits.cpu`
- `limits.memory`
- `limits.processes`
- 根磁盘 `size`
- 每个 project 只允许一个实例

更大的租户示例：

```bash
python3 scripts/incus_tenant.py create buildbot \
  --cpus 8 \
  --memory 16GiB \
  --disk 120GiB \
  --processes 16384 \
  --ui-host-port 15124
```

VM 租户示例：

```bash
python3 scripts/incus_tenant.py create paid-01 \
  --type vm \
  --cpus 4 \
  --memory 8GiB \
  --disk 80GiB \
  --ui-host-port 15125
```

## 安装来源

默认情况下，cloud-init 会通过公开 installer 安装最新版 Vibe Remote。测试某个分支或 fork 时可以指定包来源：

```bash
python3 scripts/incus_tenant.py create branch-test \
  --install-package-spec 'git+https://github.com/cyhhao/vibe-remote.git@master' \
  --ui-host-port 15126
```

## 安全边界

租户拥有的是自己 Ubuntu 环境里的权限，不是宿主机权限。不要把宿主机 Incus socket、`incus-admin` 组、宿主机 Docker socket、宿主机 SSH key 或宿主机 secret 文件暴露给租户。

container 租户会共享宿主机内核。面向陌生付费用户时，优先使用 `--type vm`，并配合严格资源限制、网络出口策略、备份恢复和监控之后，再把它视为生产 SaaS 隔离方案。
