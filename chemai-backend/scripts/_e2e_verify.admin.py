"""_e2e_verify.admin.py —— admin-console-p0 的 API 级端到端验证。

前置：后端已在 127.0.0.1:8000 运行、且 init_db 种子账号存在
（admin/admin123、qian/qian123456、superadmin/super123456）。

覆盖（spec 的 e2e 场景）：
  1. admin 登录落地依据：菜单含 5 个管理键（teachers/classes 已拆分）且排在教师菜单后
  2. teacher 菜单不含任何管理键
  3. /api/admin/overview：admin 200 + 教师待审字段；teacher 403
  4. /api/teacher-applications：teacher 403（矩阵门，无函数体第二套检查）
  5. super_admin 未提权 → data_scope_empty=true
  6. 教师入驻全流程：apply → admin 审批 → 新教师凭默认密码登录 → 菜单断言
  7. 重置密码留痕 → /api/logs 可查（admin 可读，teacher 403）

用法：python scripts/_e2e_verify.admin.py [BASE_URL]
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASSED = []
FAILED = []


def call(method: str, path: str, token: str = None, body: dict = None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def check(name: str, cond: bool, detail: str = ""):
    (PASSED if cond else FAILED).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def login(username: str, password: str):
    status, body = call("POST", "/api/auth/login", body={"username": username, "password": password})
    assert status == 200 and body.get("success"), f"登录失败 {username}: {body}"
    return body["user"], body["token"]


def main():
    print(f"== admin-console-p0 e2e @ {BASE} ==")

    # 1/2. 菜单断言（登录落地依据）
    # admin-shell-v2：管理菜单拆为 teachers/classes，admin_users 退役；分组是展示层
    admin_user, admin_token = login("admin", "admin123")
    menus = admin_user.get("menus", [])
    check("admin 菜单含五个管理键", all(k in menus for k in
          ("admin_overview", "admin_teachers", "admin_classes", "admin_school", "admin_logs")))
    check("admin_users 已退役", "admin_users" not in menus)
    check("管理键排在教师菜单后", "admin_overview" in menus and (
        "kmap" not in menus or menus.index("admin_overview") > menus.index("kmap")))

    teacher_user, teacher_token = login("qian", "qian123456")
    t_menus = teacher_user.get("menus", [])
    check("teacher 菜单不含任何管理键", not any(k.startswith("admin_") for k in t_menus))

    # 3. 概览端点角色行为（旧端点保留；八图是新端点）
    status, body = call("GET", "/api/admin/overview", admin_token)
    check("admin overview 200 全字段", status == 200 and "teacher_applications" in body.get("pending", {}))
    status, _ = call("GET", "/api/admin/overview", teacher_token)
    check("teacher overview 403", status == 403)

    # 3b. 八图端点：契约 + 权限 + 诚实空态字段
    status, body = call("GET", "/api/admin/overview-charts", admin_token)
    ok_charts = status == 200 and all(
        k in body for k in ("kpi", "trend", "parent_funnel", "class_compare",
                            "kp_heatmap", "student_scatter", "weak_kps",
                            "score_dist", "warning"))
    check("admin 八图 200 且契约完整", ok_charts)
    check("八图 KPI 含家长激活", "parent_accounts" in (body.get("kpi") or {}))
    check("预警不画处理率（闭环未接入时明示）",
          body.get("warning", {}).get("closed_loop_available") is False
          and body.get("warning", {}).get("processed") == 0)
    status, _ = call("GET", "/api/admin/overview-charts", teacher_token)
    check("teacher 八图 403", status == 403)

    # 4. 申请列表矩阵门
    status, _ = call("GET", "/api/teacher-applications", teacher_token)
    check("teacher 申请列表 403", status == 403)

    # 5. 超管空态
    _, super_token = login("superadmin", "super123456")
    status, body = call("GET", "/api/admin/overview", super_token)
    check("super_admin 未提权 data_scope_empty", status == 200 and body.get("data_scope_empty") is True)

    # 6. 教师入驻全流程
    phone = f"139{int(time.time()) % 100000000:08d}"
    status, body = call("POST", "/api/auth/apply",
                        body={"name": "e2e教师", "phone": phone, "school": "e2e中学", "subject": "化学"})
    check("教师申请提交", status == 200 and body.get("success") is not False, str(body))
    status, body = call("GET", "/api/teacher-applications?status=pending", admin_token)
    apps = [a for a in body.get("applications", []) if a["phone"] == phone]
    check("admin 可见待审申请", status == 200 and len(apps) == 1)
    if apps:
        status, body = call("POST", f"/api/teacher-applications/{apps[0]['id']}/approve", admin_token)
        check("审批通过并创建账号", status == 200 and body.get("success") and body.get("default_password"))
        new_user, _ = login(phone, body["default_password"])
        check("新教师凭默认密码登录且无管理菜单",
              new_user.get("role") == "teacher" and not any(k.startswith("admin_") for k in new_user.get("menus", [])))

    # 7. 重置密码留痕
    status, body = call("GET", "/api/users/students", admin_token)
    students = body.get("students", [])
    if students:
        sid = students[0]["student_id"]
        status, _ = call("POST", f"/api/users/student/{sid}/reset-password", admin_token)
        check("重置密码成功", status == 200)
        status, body = call("GET", "/api/logs?action=student_reset_password", admin_token)
        check("日志页可追溯重置密码", status == 200 and body.get("total", 0) >= 1)
    status, _ = call("GET", "/api/logs", teacher_token)
    check("teacher 读日志 403", status == 403)

    print(f"\n== 结果：{len(PASSED)} passed, {len(FAILED)} failed ==")
    if FAILED:
        print("失败项：", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    main()
