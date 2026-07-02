import streamlit as st
import pandas as pd
import requests
import uuid
from lark_oapi.api.bitable.v1 import *
from lark_oapi import Client

# ------------------------------ 飞书配置（从 secrets 读取） ------------------------------
FEISHU_APP_ID = st.secrets["FEISHU_APP_ID"]
FEISHU_APP_SECRET = st.secrets["FEISHU_APP_SECRET"]
FEISHU_APP_TOKEN = st.secrets["FEISHU_APP_TOKEN"]
REDIRECT_URI = st.secrets["REDIRECT_URI"]  # 你的 Streamlit Cloud 域名

client = Client.builder().app_id(FEISHU_APP_ID).app_secret(FEISHU_APP_SECRET).build()

# ------------------------------ 飞书 OAuth 登录 ------------------------------
def login():
    if "user" not in st.session_state:
        st.session_state.user = None

    if st.session_state.user is None:
        # 检查 URL 参数是否有授权回调 code
        query_params = st.query_params
        if "code" in query_params:
            code = query_params["code"]
            # 换取 access_token
            token_url = "https://open.feishu.cn/open-apis/authen/v1/access_token"
            headers = {"Content-Type": "application/json"}
            body = {
                "app_id": FEISHU_APP_ID,
                "app_secret": FEISHU_APP_SECRET,
                "grant_type": "authorization_code",
                "code": code,
            }
            resp = requests.post(token_url, json=body, headers=headers).json()
            if resp.get("code") == 0:
                access_token = resp["data"]["access_token"]
                # 获取用户信息
                user_url = "https://open.feishu.cn/open-apis/authen/v1/user_info"
                headers = {"Authorization": f"Bearer {access_token}"}
                user_resp = requests.get(user_url, headers=headers).json()
                if user_resp.get("code") == 0:
                    user_info = user_resp["data"]
                    st.session_state.user = {
                        "open_id": user_info["open_id"],
                        "name": user_info.get("name", "用户"),
                        "avatar": user_info.get("avatar_url", ""),
                    }
                    st.rerun()
                else:
                    st.error("获取用户信息失败，请重试")
            else:
                st.error(f"登录失败：{resp.get('msg', '未知错误')}")
            st.stop()
        else:
            # 生成飞书授权链接
            state = str(uuid.uuid4())
            auth_url = (
                f"https://open.feishu.cn/open-apis/authen/v1/index?"
                f"app_id={FEISHU_APP_ID}&redirect_uri={REDIRECT_URI}&state={state}"
            )
            st.markdown(f"""
                <div style="text-align:center; margin-top:50px;">
                    <h2>📦 彩盒印刷报价系统</h2>
                    <a href="{auth_url}" target="_self">
                        <button style="padding:10px 20px;font-size:16px;">🔐 使用飞书账号登录</button>
                    </a>
                </div>
            """, unsafe_allow_html=True)
            st.stop()
    else:
        # 已登录
        st.sidebar.success(f"👤 {st.session_state.user['name']}")
        if st.sidebar.button("退出登录"):
            st.session_state.user = None
            st.query_params.clear()
            st.rerun()

# ------------------------------ 飞书表格工具函数（含用户隔离） ------------------------------
def to_text(val):
    return [{"text": str(val)}] if pd.notna(val) else None

def from_text(field_val):
    if isinstance(field_val, list) and field_val:
        return field_val[0].get("text", "")
    return ""

def load_table(table_name, columns, user_id):
    """读取当前用户的数据"""
    resp = client.bitable.v1.app_table.list(AppTokenRequest.builder().app_token(FEISHU_APP_TOKEN).build())
    tables = resp.data.items
    table_map = {t.name: t.table_id for t in tables}
    table_id = table_map.get(table_name)
    if not table_id:
        return pd.DataFrame(columns=columns + ["record_id"])

    filter_cond = {
        "field": "user_id",
        "operator": "is",
        "value": [user_id]
    }
    all_records = []
    page_token = None
    while True:
        req = SearchAppTableRecordRequest.builder() \
            .app_token(FEISHU_APP_TOKEN) \
            .table_id(table_id) \
            .filter(filter_cond) \
            .page_size(100) \
            .page_token(page_token) \
            .build()
        resp = client.bitable.v1.app_table_record.search(req)
        if not resp.success():
            break
        for item in resp.data.items:
            fields = {}
            for col in columns:
                val = item.fields.get(col)
                if col in ("gsm", "price_per_ton", "price_per_sqm", "thickness_c", "formula_coef", "formula_price"):
                    fields[col] = val if isinstance(val, (int, float)) else 0
                else:
                    fields[col] = from_text(val)
            fields["record_id"] = item.record_id
            all_records.append(fields)
        if not resp.data.has_more:
            break
        page_token = resp.data.page_token
    return pd.DataFrame(all_records, columns=columns + ["record_id"])

def save_table(table_name, df, columns, user_id):
    """保存当前用户数据"""
    old_df = load_table(table_name, columns, user_id)
    old_ids = set(old_df["record_id"].tolist())
    resp = client.bitable.v1.app_table.list(AppTokenRequest.builder().app_token(FEISHU_APP_TOKEN).build())
    tables = resp.data.items
    table_map = {t.name: t.table_id for t in tables}
    table_id = table_map.get(table_name)
    if not table_id:
        return

    new_ids = set()
    for _, row in df.iterrows():
        record_id = row.get("record_id")
        fields = {}
        for col in columns:
            val = row[col]
            if col in ("name", "pricing", "type", "key", "value"):
                fields[col] = to_text(val) if pd.notna(val) else None
            else:
                fields[col] = float(val) if pd.notna(val) else None
        fields["user_id"] = user_id  # 强制绑定当前用户
        if record_id and record_id in old_ids:
            req = UpdateAppTableRecordRequest.builder() \
                .app_token(FEISHU_APP_TOKEN) \
                .table_id(table_id) \
                .record_id(record_id) \
                .request_body(AppTableRecord.builder().fields(fields).build()) \
                .build()
            client.bitable.v1.app_table_record.update(req)
            new_ids.add(record_id)
        else:
            req = CreateAppTableRecordRequest.builder() \
                .app_token(FEISHU_APP_TOKEN) \
                .table_id(table_id) \
                .request_body(AppTableRecord.builder().fields(fields).build()) \
                .build()
            resp2 = client.bitable.v1.app_table_record.create(req)
            if resp2.success():
                new_ids.add(resp2.data.record.record_id)

    # 删除多余记录
    for rid in old_ids - new_ids:
        client.bitable.v1.app_table_record.delete(
            DeleteAppTableRecordRequest.builder()
            .app_token(FEISHU_APP_TOKEN).table_id(table_id).record_id(rid).build()
        )

def load_settings(user_id):
    df = load_table("settings", ["key", "value"], user_id)
    settings = {}
    for _, row in df.iterrows():
        key = row["key"]
        val = row["value"]
        if key in ("default_colors", "low_vol_threshold", "max_base_colors"):
            settings[key] = int(float(val)) if val else 0
        elif key in ("box_mounting_per_pcs", "plate_mounting_per_1000", "profit_percent",
                     "tax_rate", "default_quantity_1000", "low_base_price", "low_extra_price",
                     "high_base_price", "high_extra_price"):
            settings[key] = float(val) if val else 0.0
        else:
            settings[key] = val
    # 默认值
    if not settings:
        settings = {
            "box_mounting_per_pcs": 0.2,
            "plate_mounting_per_1000": 150.0,
            "profit_percent": 10.0,
            "tax_rate": 13.0,
            "default_colors": 4,
            "default_quantity_1000": 3.0,
            "low_vol_threshold": 2000,
            "low_base_price": 50,
            "low_extra_price": 100,
            "high_base_price": 25,
            "high_extra_price": 50,
            "max_base_colors": 4,
        }
        save_settings(settings, user_id)
    return settings

def save_settings(settings_dict, user_id):
    df = pd.DataFrame(list(settings_dict.items()), columns=["key", "value"])
    save_table("settings", df, ["key", "value"], user_id)

# ------------------------------ 默认数据初始化 ------------------------------
def init_default_data(user_id):
    """为新用户创建常用物料模板"""
    if st.button("📥 初始化默认物料数据"):
        papers = pd.DataFrame([
            {"name": "灰卡250g", "gsm": 250, "type": "灰卡", "price_per_ton": 4000.0},
            {"name": "灰卡300g", "gsm": 300, "type": "灰卡", "price_per_ton": 4600.0},
        ])
        films = pd.DataFrame([
            {"name": "亚光膜", "pricing": "area", "price_per_sqm": 1.2, "gsm": 0, "price_per_ton": 0},
            {"name": "光膜", "pricing": "area", "price_per_sqm": 1.0, "gsm": 0, "price_per_ton": 0},
        ])
        corrugated = pd.DataFrame([
            {"name": "东社高强瓦楞", "price_per_sqm": 1.8},
            {"name": "普通瓦楞", "price_per_sqm": 1.2},
        ])
        coatings = pd.DataFrame([
            {"name": "上釉", "type": "varnish", "price_per_sqm": 0.8},
            {"name": "磨光", "type": "calendering", "price_per_sqm": 1.0},
        ])
        specialties = pd.DataFrame([
            {"name": "烫金", "type": "hot_stamping", "price_per_sqm": 5.0},
            {"name": "逆向", "type": "reverse", "price_per_sqm": 3.0},
        ])
        plates = pd.DataFrame([
            {"name": "菲林0.05mm", "thickness_c": 5.0, "formula_coef": 14.0, "formula_price": 0.5},
            {"name": "菲林0.10mm", "thickness_c": 10.0, "formula_coef": 14.0, "formula_price": 0.6},
        ])
        save_table("papers", papers, ["name","gsm","type","price_per_ton"], user_id)
        save_table("films", films, ["name","pricing","price_per_sqm","gsm","price_per_ton"], user_id)
        save_table("corrugated", corrugated, ["name","price_per_sqm"], user_id)
        save_table("coatings", coatings, ["name","type","price_per_sqm"], user_id)
        save_table("specialties", specialties, ["name","type","price_per_sqm"], user_id)
        save_table("plates", plates, ["name","thickness_c","formula_coef","formula_price"], user_id)
        st.success("默认物料已导入！")
        st.rerun()

# ------------------------------ 程序入口 ------------------------------
login()  # 必须先登录

# 获取当前用户
current_user = st.session_state.user["open_id"]

# 页面设置
st.set_page_config(page_title="彩盒印刷报价系统", layout="wide")
st.title("📦 彩盒印刷报价系统")

# 左侧开发者模式
dev_mode = st.sidebar.checkbox("🔧 开发者模式", value=False)

menu = st.sidebar.selectbox("导航", ["📋 报价计算", "🗃️ 资料库管理"])

# 加载用户数据（缓存到 session）
if "papers" not in st.session_state:
    st.session_state.papers = load_table("papers", ["name","gsm","type","price_per_ton"], current_user)
if "films" not in st.session_state:
    st.session_state.films = load_table("films", ["name","pricing","price_per_sqm","gsm","price_per_ton"], current_user)
if "corrugated" not in st.session_state:
    st.session_state.corrugated = load_table("corrugated", ["name","price_per_sqm"], current_user)
if "coatings" not in st.session_state:
    st.session_state.coatings = load_table("coatings", ["name","type","price_per_sqm"], current_user)
if "specialties" not in st.session_state:
    st.session_state.specialties = load_table("specialties", ["name","type","price_per_sqm"], current_user)
if "plates" not in st.session_state:
    st.session_state.plates = load_table("plates", ["name","thickness_c","formula_coef","formula_price"], current_user)
if "settings" not in st.session_state:
    st.session_state.settings = load_settings(current_user)

# ============================== 报价计算页面（略作修改，调用用户数据） ==============================
if menu == "📋 报价计算":
    settings = st.session_state.settings
    st.header("成本估算与报价")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📐 基本参数")
        paper_length = st.number_input("纸张长度 (mm)", 100, 3000, 860, step=1)
        paper_width  = st.number_input("纸张宽度 (mm)", 100, 3000, 460, step=1)
        quantity_1000 = st.number_input("数量 (千张)", 0.01, value=float(settings["default_quantity_1000"]), step=0.1)
        sheets = quantity_1000 * 1000
        reams_for_print = max(1.0, quantity_1000)
        output_per_sheet = st.number_input("每张纸出数 (个盒子)", 1, value=3, step=1)
        total_boxes = sheets * output_per_sheet

        papers = st.session_state.papers
        if papers.empty:
            st.warning("尚未添加纸张，请前往资料库管理添加。")
        else:
            paper_names = papers["name"].tolist()
            sel_paper_name = st.selectbox("纸张材质", paper_names)
            sel_paper = papers[papers["name"] == sel_paper_name].iloc[0]
            if dev_mode:
                st.caption(f"克重 {sel_paper['gsm']} g/m²，吨价 {sel_paper['price_per_ton']:.0f}")

        colors = st.number_input("印刷色数", 1, 12, int(settings["default_colors"]))

    with col2:
        st.subheader("🛠️ 后道工艺")
        # 覆膜
        use_film = st.checkbox("覆膜")
        film_price = 0.0
        if use_film:
            films = st.session_state.films
            if films.empty:
                st.warning("无膜数据")
            else:
                film_name = st.selectbox("膜种类", films["name"].tolist())
                film_row = films[films["name"] == film_name].iloc[0]
                if film_row["pricing"] == "area":
                    film_price = film_row["price_per_sqm"]
                else:
                    film_price = film_row["gsm"] * film_row["price_per_ton"] / 1_000_000
                if dev_mode:
                    st.caption(f"膜单价：{film_price:.2f}")

        # 瓦楞
        use_corr = st.checkbox("裱瓦楞")
        corr_price = 0.0
        if use_corr:
            corrs = st.session_state.corrugated
            if corrs.empty:
                st.warning("无瓦楞数据")
            else:
                corr_name = st.selectbox("瓦楞种类", corrs["name"].tolist())
                corr_price = corrs[corrs["name"]==corr_name]["price_per_sqm"].iloc[0]
                if dev_mode:
                    st.caption(f"瓦楞单价：{corr_price:.2f}")

        # 上釉
        use_coat1 = st.checkbox("上釉")
        coat1_price = 0.0
        if use_coat1:
            coats = st.session_state.coatings[st.session_state.coatings["type"]=="varnish"]
            if coats.empty:
                st.warning("无上釉数据")
            else:
                coat1_price = coats.iloc[0]["price_per_sqm"]
                if dev_mode:
                    st.caption(f"上釉单价：{coat1_price:.2f}")

        # 磨光
        use_coat2 = st.checkbox("磨光")
        coat2_price = 0.0
        if use_coat2:
            coats = st.session_state.coatings[st.session_state.coatings["type"]=="calendering"]
            if coats.empty:
                st.warning("无磨光数据")
            else:
                coat2_price = coats.iloc[0]["price_per_sqm"]
                if dev_mode:
                    st.caption(f"磨光单价：{coat2_price:.2f}")

        # 特殊工艺
        use_spec = st.checkbox("特殊工艺")
        spec_total = 0.0
        if use_spec:
            specs = st.session_state.specialties
            if specs.empty:
                st.warning("无特殊工艺数据")
            else:
                sel_specs = st.multiselect("选择工艺", specs["name"].tolist())
                for s in sel_specs:
                    row = specs[specs["name"]==s].iloc[0]
                    spec_total += row["price_per_sqm"]
                if dev_mode:
                    st.caption(f"特殊工艺单价合计：{spec_total:.2f}")

        # 菲林
        use_plate = st.checkbox("贴菲林")
        if use_plate:
            plates = st.session_state.plates
            if plates.empty:
                st.warning("无菲林数据")
            else:
                plate_name = st.selectbox("菲林种类", plates["name"].tolist())
                plate_row = plates[plates["name"]==plate_name].iloc[0]
                if dev_mode:
                    st.caption(f"厚度 {plate_row['thickness_c']} C，系数 {plate_row['formula_coef']}，单价 {plate_row['formula_price']}")
                plate_len = st.number_input("菲林长 (cm)", value=paper_length/10., step=0.1)
                plate_wid = st.number_input("菲林宽 (cm)", value=paper_width/10., step=0.1)

        # 工费
        st.subheader("💵 工费设置")
        box_mount = st.number_input("贴盒工费 (元/个)", value=settings["box_mounting_per_pcs"], step=0.01)
        plate_mount = 0.0
        if use_plate:
            plate_mount = st.number_input("贴菲林工费 (元/千张)", value=settings["plate_mounting_per_1000"], step=10.0)
        custom_fee = st.number_input("自定义加工费 (元/千张)", value=0.0, step=10.0)

    if st.button("🧮 计算报价"):
        area = (paper_length/1000)*(paper_width/1000)
        # 纸张成本
        if papers.empty:
            cost_paper = 0
        else:
            ton = sel_paper["price_per_ton"]
            single_cost = area * sel_paper["gsm"] * ton / 1_000_000
            cost_paper = single_cost * sheets
        # 其他成本
        cost_film = area * film_price * sheets if use_film else 0
        cost_corr = area * corr_price * sheets if use_corr else 0
        cost_coat1 = area * coat1_price * sheets if use_coat1 else 0
        cost_coat2 = area * coat2_price * sheets if use_coat2 else 0
        cost_spec = area * spec_total * sheets if use_spec else 0
        # 菲林
        cost_plate = 0
        if use_plate and not plates.empty:
            single_plate = (plate_len * plate_wid * plate_row["thickness_c"] * plate_row["formula_coef"] / 10000) * plate_row["formula_price"]
            cost_plate = single_plate * output_per_sheet * sheets
        # 印刷
        low_th = settings["low_vol_threshold"]
        if sheets <= low_th:
            if colors <= settings["max_base_colors"]:
                ream_price = colors * settings["low_base_price"]
            else:
                ream_price = settings["max_base_colors"]*settings["low_base_price"] + (colors-settings["max_base_colors"])*settings["low_extra_price"]
        else:
            if colors <= settings["max_base_colors"]:
                ream_price = colors * settings["high_base_price"]
            else:
                ream_price = settings["max_base_colors"]*settings["high_base_price"] + (colors-settings["max_base_colors"])*settings["high_extra_price"]
        cost_print = reams_for_print * ream_price
        cost_box = total_boxes * box_mount
        cost_plate_m = plate_mount * max(1.0, quantity_1000) if use_plate else 0
        custom_total = custom_fee * quantity_1000
        total = cost_paper + cost_film + cost_corr + cost_coat1 + cost_coat2 + cost_spec + cost_plate + cost_print + cost_box + cost_plate_m + custom_total
        profit = total * settings["profit_percent"] / 100
        price_no_tax = total + profit
        price_with_tax = price_no_tax * (1 + settings["tax_rate"]/100)
        unit_no_tax = price_no_tax / total_boxes
        unit_with_tax = price_with_tax / total_boxes

        if dev_mode:
            col_r1, col_r2, col_r3 = st.columns(3)
            col_r1.metric("直接成本单价", f"{total/total_boxes:.3f}")
            col_r2.metric("不含税单价", f"{unit_no_tax:.3f}")
            col_r3.metric("含税单价", f"{unit_with_tax:.3f}")
            st.caption(f"总盒子数 {total_boxes:.0f}，印刷计费令数 {reams_for_print}")
            with st.expander("成本明细"):
                st.write(pd.DataFrame({
                    "项目": ["纸张","覆膜","瓦楞","上釉","磨光","特殊","菲林","印刷","贴盒","贴菲林","自定义","利润"],
                    "金额": [cost_paper,cost_film,cost_corr,cost_coat1,cost_coat2,cost_spec,cost_plate,cost_print,cost_box,cost_plate_m,custom_total,profit]
                }))
        else:
            col_r2, col_r3 = st.columns(2)
            col_r2.metric("不含税单价 (元/个)", f"{unit_no_tax:.3f}")
            col_r3.metric("含税单价 (元/个)", f"{unit_with_tax:.3f}")

# ============================== 资料库管理 ==============================
elif menu == "🗃️ 资料库管理":
    st.header("资料库管理")
    # 首次使用提示
    if st.session_state.papers.empty and st.session_state.films.empty:
        st.info("您的资料库为空，可以使用下方按钮导入默认数据。")
        init_default_data(current_user)

    tabs = st.tabs(["纸张", "膜", "瓦楞", "上釉/磨光", "特殊工艺", "菲林", "全局设置"])

    # 纸张
    with tabs[0]:
        edited = st.data_editor(st.session_state.papers.drop(columns=["record_id"], errors="ignore"),
            num_rows="dynamic", column_config={
                "name":"名称","gsm":"克重","type":"类型","price_per_ton":"吨价(元/吨)"
            }, key="paper_ed")
        if st.button("保存纸张"):
            st.session_state.papers = edited
            save_table("papers", edited, ["name","gsm","type","price_per_ton"], current_user)
            st.success("已保存")

    # 膜
    with tabs[1]:
        edited = st.data_editor(st.session_state.films.drop(columns=["record_id"], errors="ignore"),
            num_rows="dynamic", column_config={
                "name":"名称","pricing":"计价","price_per_sqm":"面积单价","gsm":"克重","price_per_ton":"吨价"
            }, key="film_ed")
        if st.button("保存膜"):
            st.session_state.films = edited
            save_table("films", edited, ["name","pricing","price_per_sqm","gsm","price_per_ton"], current_user)
            st.success("已保存")

    # 瓦楞
    with tabs[2]:
        edited = st.data_editor(st.session_state.corrugated.drop(columns=["record_id"], errors="ignore"),
            num_rows="dynamic", column_config={"name":"名称","price_per_sqm":"单价"}, key="corr_ed")
        if st.button("保存瓦楞"):
            st.session_state.corrugated = edited
            save_table("corrugated", edited, ["name","price_per_sqm"], current_user)
            st.success("已保存")

    # 表面处理
    with tabs[3]:
        edited = st.data_editor(st.session_state.coatings.drop(columns=["record_id"], errors="ignore"),
            num_rows="dynamic", column_config={
                "name":"名称","type":"类型","price_per_sqm":"单价"
            }, key="coat_ed")
        if st.button("保存表面处理"):
            st.session_state.coatings = edited
            save_table("coatings", edited, ["name","type","price_per_sqm"], current_user)
            st.success("已保存")

    # 特殊工艺
    with tabs[4]:
        edited = st.data_editor(st.session_state.specialties.drop(columns=["record_id"], errors="ignore"),
            num_rows="dynamic", column_config={
                "name":"名称","type":"类型","price_per_sqm":"单价"
            }, key="spec_ed")
        if st.button("保存特殊工艺"):
            st.session_state.specialties = edited
            save_table("specialties", edited, ["name","type","price_per_sqm"], current_user)
            st.success("已保存")

    # 菲林
    with tabs[5]:
        edited = st.data_editor(st.session_state.plates.drop(columns=["record_id"], errors="ignore"),
            num_rows="dynamic", column_config={
                "name":"名称","thickness_c":"厚度","formula_coef":"系数","formula_price":"单价"
            }, key="plate_ed")
        if st.button("保存菲林"):
            st.session_state.plates = edited
            save_table("plates", edited, ["name","thickness_c","formula_coef","formula_price"], current_user)
            st.success("已保存")

    # 全局设置
    with tabs[6]:
        st.subheader("印刷规则及利润")
        s = st.session_state.settings
        n_box = st.number_input("贴盒工费 (元/个)", value=s["box_mounting_per_pcs"], step=0.01)
        n_plate = st.number_input("贴菲林工费 (元/千张)", value=s["plate_mounting_per_1000"], step=10.0)
        n_profit = st.number_input("利润 (%)", value=s["profit_percent"], step=0.5)
        n_tax = st.number_input("税率 (%)", value=s["tax_rate"], step=0.5)
        n_colors = st.number_input("默认色数", value=s["default_colors"], min_value=1, max_value=12)
        n_qty = st.number_input("默认数量 (千张)", value=s["default_quantity_1000"], step=0.1)
        n_th = st.number_input("小批量上限 (张)", value=s["low_vol_threshold"], step=100)
        n_max = st.number_input("基础色数", value=s["max_base_colors"], min_value=1, max_value=8)
        n_lb = st.number_input("≤上限 基础每色令价", value=s["low_base_price"])
        n_le = st.number_input("≤上限 超色加价", value=s["low_extra_price"])
        n_hb = st.number_input(">上限 基础每色令价", value=s["high_base_price"])
        n_he = st.number_input(">上限 超色加价", value=s["high_extra_price"])
        if st.button("保存全局设置"):
            new_s = {
                "box_mounting_per_pcs": n_box, "plate_mounting_per_1000": n_plate,
                "profit_percent": n_profit, "tax_rate": n_tax,
                "default_colors": int(n_colors), "default_quantity_1000": n_qty,
                "low_vol_threshold": int(n_th), "max_base_colors": int(n_max),
                "low_base_price": n_lb, "low_extra_price": n_le,
                "high_base_price": n_hb, "high_extra_price": n_he,
            }
            st.session_state.settings = new_s
            save_settings(new_s, current_user)
            st.success("全局设置已保存")