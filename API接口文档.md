# 数字人 API 接口文档

版本：`server1.4.0.py`  
更新时间：2026-09-22

## 1. 服务说明

默认服务地址：

```text
http://<服务器地址>:5000
```

当前 Flask 服务监听 `0.0.0.0:5000`。所有接口均使用 `POST`，请求体为 JSON 的接口需要设置：

```http
Content-Type: application/json
```

接口成功通常返回：

```json
{"result": "Success"}
```

异常通常返回：

```json
{"result": "Failed"}
```

当前实现大多数业务错误也返回 HTTP 200，客户端应同时检查 `result` 字段。

## 2. 推荐调用流程

1. `/Login` 登录并初始化用户目录。
2. 上传图片、PPT 视频、音频和备注等素材。
3. 选择模型并保存配置。
4. 调用推理接口。
5. 使用 `/Get_State` 查询异步任务状态。
6. 调用 `/PPT_Video_Merge` 或 `/PPT_Video_Merge_Select_Into` 合成最终视频。
7. 使用 `/Pull_Video_Merge` 下载 Base64 视频。

用户目录位于服务器：`Result/*/<User>` 和 `Data/<User>`。

## 3. 登录与状态

### POST `/Login`

验证账号并初始化用户目录。

请求：

```json
{"User": "Test", "Password": "123000"}
```

成功响应：`{"result":"Success"}`。

### POST `/Get_State`

查询任务状态。

请求：

```json
{"User": "Test", "Task": "Audio_Video_Inference"}
```

常用任务名：`VITS_Train`、`VITS_Inference`、`Audio_Video_Inference`、`Video_Merge`。任务尚未写入状态时会返回 `Failed`。

## 4. 素材上传

### POST `/Send_Image`

JSON 上传 Base64 图片。字段：`User`、`Img`。服务会保存为用户目录下的 `Image.png`。

```json
{"User":"Test", "Img":"<base64 图片数据>"}
```

### POST `/Send_Teacher_Video`

`multipart/form-data` 上传教师视频。

| 字段 | 类型 | 说明 |
|---|---|---|
| `Json` | 文本 | `{"User":"Test"}` |
| `File` | 文件 | 视频文件 |

保存为 `Video.mp4`。

### POST `/Send_Video`

`multipart/form-data` 上传 PPT 视频。

| 字段 | 类型 | 说明 |
|---|---|---|
| `Json` | 文本 | `{"User":"Test"}` |
| `File` | 文件 | PPT 导出视频 |

保存为 `PPT_Video.mp4`。

### POST `/Send_PPT_Audio`

`multipart/form-data` 上传用于插入 PPT 的音频。

| 字段 | 类型 | 说明 |
|---|---|---|
| `Json` | 文本 | `{"User":"Test", "Audio_Name":"0.wav"}` |
| `File` | 文件 | WAV/音频文件 |

### POST `/Send_Tarin_Audio`

`multipart/form-data` 上传 VITS 训练音频。字段格式同上，`Json` 使用 `User`、`Audio_Name`。

### POST `/Send_Ref_Wav_And_Text`

`multipart/form-data` 上传 VITS 参考音频。

| 字段 | 类型 | 说明 |
|---|---|---|
| `Json` | 文本 | `{"User":"Test", "Ref_Text":"参考音频对应的文字"}` |
| `File` | 文件 | 参考音频 |

## 5. PPT 和人物配置

### POST `/Send_PPT_Remakes`

保存 PPT 批注。请求字段：`User`、`PPT_Remakes`。`PPT_Remakes` 应为 JSON 对象或数组。

### POST `/Send_People_Location`

保存数字人插入页码。请求字段：`User`、`People_Location`。示例：

```json
{"User":"Test", "People_Location":{"0":"True", "1":"False"}}
```

### POST `/Send_Config`

保存 VITS 和 SadTalker 配置。请求字段：`User`、`VITS_Config`、`SadTalker_Config`。

### POST `/Send_Wav2Lip_Config`

保存 Wav2Lip 配置。请求字段：`User`、`Wav2Lip_Config`。

### POST `/Send_Select_VITS_Model`

选择预训练 VITS 模型。请求：

```json
{"User":"Test", "Index":0}
```

### POST `/Send_Select_Train_VITS_Model`

选择用户训练模型。请求：`{"User":"Test"}`，模型文件名使用用户名称。

## 6. 音频和模型训练

### POST `/Recive_Wav_Time`

读取 VITS 输出音频时长。请求：`{"User":"Test"}`。

### POST `/Recive_User_Wav_Time`

读取用户 PPT 音频时长。请求：`{"User":"Test"}`。

### POST `/Train_VITS_Model`

异步训练 VITS。请求：

```json
{"User":"Test", "Label":{"0.wav":"第一句文字"}}
```

返回 `{"result":"VITS_Train"}` 后，通过 `/Get_State` 查询 `VITS_Train`。

### POST `/Get_Train_VITS_Model_Name`

检查用户训练模型是否存在。请求：`{"User":"Test"}`。

## 7. 推理接口

### POST `/Get_Test_Inference`

生成固定测试语句的测试视频，并直接返回 Base64 视频：

```json
{"User":"Test"}
```

### POST `/Get_Inference_VITS`

生成单条测试语音。请求：`{"User":"Test"}`。

### POST `/Get_Inference_VITS_Multiple`

根据用户目录中的参考音频生成多条 VITS 音频。返回后查询 `VITS_Inference`。

### POST `/Get_Inference_VITS_Sadtalker`

异步执行 VITS + SadTalker，生成数字人视频。请求：`{"User":"Test"}`，返回后查询 `Audio_Video_Inference`。

### POST `/Get_Inference_User_Audio_Sadtalker`

使用用户上传音频执行 SadTalker。请求：`{"User":"Test"}`，返回后查询 `Audio_Video_Inference`。

### POST `/Get_Inference_VITS_Wav2Lip`

执行 VITS + Wav2Lip。请求：`{"User":"Test"}`。

### POST `/Get_Inference_User_Audio_Wav2Lip`

使用用户音频执行 Wav2Lip。请求：`{"User":"Test"}`，返回后查询 `Audio_Video_Inference`。

## 8. 视频合成与下载

### POST `/PPT_Video_Merge`

将所有页插入数字人并异步合成最终视频。请求：`{"User":"Test"}`，返回后查询 `Video_Merge`。

### POST `/PPT_Video_Merge_Select_Into`

按照 `/Send_People_Location` 的选择插入数字人。请求：`{"User":"Test"}`，返回后查询 `Video_Merge`。

### POST `/PPT_Video_Merge_No_Into`

不插入数字人，仅将 PPT 视频和音频合成。请求：`{"User":"Test"}`。

### POST `/Pull_Video_Merge`

获取最终视频 Base64。请求：`{"User":"Test"}`，响应 `result` 为 Base64 字符串。

### POST `/Pull_VITS_Audio`

获取测试 VITS 音频 Base64。请求：`{"User":"Test"}`，响应 `result` 为 Base64 字符串。

## 9. curl 示例

```bash
BASE=http://127.0.0.1:5000

curl "$BASE/Login" \
  -H 'Content-Type: application/json' \
  -d '{"User":"Test","Password":"123000"}'

curl "$BASE/Get_State" \
  -H 'Content-Type: application/json' \
  -d '{"User":"Test","Task":"Audio_Video_Inference"}'

curl "$BASE/Send_Video" \
  -F 'Json={"User":"Test"}' \
  -F 'File=@PPT_Video.mp4'

curl "$BASE/Get_Inference_VITS_Sadtalker" \
  -H 'Content-Type: application/json' \
  -d '{"User":"Test"}'

curl "$BASE/PPT_Video_Merge" \
  -H 'Content-Type: application/json' \
  -d '{"User":"Test"}'
```

## 10. 当前限制

- 当前运行的是 Flask 开发服务器，正式部署建议改用 Gunicorn 或 gevent。
- 推理和视频合成会占用 GPU，并且部分接口是异步任务；客户端必须轮询状态。
- 当前接口未统一使用 HTTP 错误码，必须读取 JSON 中的 `result`。
- 用户输入、文件类型和 Base64 数据目前缺少严格校验。
- 外部访问前需要在云服务器控制台放行或映射 5000 端口。
