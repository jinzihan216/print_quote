import streamlit as st
import lark_oapi as lark
from lark_oapi.api.auth import *
import time

# ================= 配置区域 (请填入你的飞书应用信息) =================
APP_ID = "cli_aacc8f3515fbdbda"   # 替换为你的 App ID
APP_SECRET = "Uta7tPKmnKMCFghTsOX5fbqJ4kSLyNYP"    # 替换为你的 App Secret
REDIRECT_URI = "https://printquote-g8gs84dbbdokatp2fdschn.streamlit.app/_stcore/auth/callback" # 必须与飞书后台配置完全一致

# 初始化飞书客户端
client = lark.Client.builder() \
    .app_id(APP_ID) \
    .app_secret(APP_SECRET) \
    .log_level(lark.LogLevel.ERROR) \
    .build()

# ================= 页面基础设置 =================
st.set_page_config(page_title="彩盒印刷智能报价系统", layout="wide")

# 初始化 Session State
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'user_info' not in st.session_state:
    st.session_state['user_info'] = None

# ================= 核心修复：登录验证与防循环逻辑 =================

def handle_login_callback():
    """处理飞书回调，防止重定向循环"""
    # 1. 获取 URL 中的 code 参数
    params = st.query_params
    auth_code = params.get("code")

    if auth_code:
        try:
            # 2. 使用 code 获取 access_token 和 refresh_token
            request = oauth.OauthTokenRequest.builder() \
                .grant_type("authorization_code") \
                .code(auth_code) \
                .build()
            
            response = client.oauth.token(request)
            
            if response.success():
                token_data = response.data
                access_token = token_data.access_token
                
                # 3. (可选) 获取用户信息
                # user_resp = client.contact.v3.user.me(lark.RequestOptions.builder().build())
                
                # 4. 标记登录成功
                st.session_state['logged_in'] = True
                st.session_state['access_token'] = access_token
                
                # 5. 【关键修复】清除 URL 中的 code 参数并强制刷新
                # 这样可以避免刷新页面时再次触发这个回调逻辑
                st.query_params.clear() 
                st.rerun() 
            else:
                st.error(f"登录失败: {response.code}, {response.msg}")
        except Exception as e:
            st.error(f"登录过程发生异常: {str(e)}")

# 在脚本最顶部执行回调检查
handle_login_callback()

# ================= 界面渲染逻辑 =================

# 如果未登录，只显示登录界面，不执行下面的任何代码
if not st.session_state.get('logged_in'):
    st.title("🔐 彩盒印刷智能报价系统")
    st.markdown("---")
    st.write("请使用飞书扫码或点击按钮登录以访问系统。")
    
    # 构建飞书 OAuth 授权链接
    # scope 根据你的需求调整，这里假设需要获取用户基本信息
    auth_url = (
        f"https://open.feishu.cn/open-apis/authen/v1/authorize?app_id={APP_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state=random_state_string" 
    )
    
    # 使用 Streamlit 的 link_button 进行跳转
    st.link_button("👉 点击前往飞书登录", auth_url, type="primary")
    
    # 【重要】在这里直接停止脚本运行！
    # 防止代码继续向下执行去渲染主界面，从而导致逻辑冲突
    st.stop() 

# ================= 主程序区域 (只有登录成功后才会执行) =================

st.title("📦 彩盒印刷智能报价系统")
st.success(f"欢迎回来！当前状态：已登录")

# 这里放置你原来的业务逻辑代码
# 例如：侧边栏、资料库管理、报价计算等...

tab1, tab2 = st.tabs(["📋 报价计算", "🗃️ 资料库管理"])

with tab1:
    st.header("快速报价")
    # ... 你的报价计算 UI ...
    st.info("此处为报价计算模块内容")

with tab2:
    st.header("基础数据维护")
    # ... 你的资料库管理 UI ...
    st.info("此处为资料库管理模块内容")

# 登出功能
if st.button("退出登录"):
    st.session_state['logged_in'] = False
    st.session_state['access_token'] = None
    st.rerun()
