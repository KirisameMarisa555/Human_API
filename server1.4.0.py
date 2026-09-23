"""
    添加可选择对指定页数插入音频
    修复了：第二次上传自定义音频没有清空上一次的音频
            数字人拼接没有贴边
            数字人太小
    增加了初始化文件夹（删除文件）
"""

import base64
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
import threading
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

from Main import *
from util.Function import Verification, Encode_Video, Write_Json

import concurrent.futures

# 创建线程池
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
course_task_lock = threading.Lock()
course_gpu_lock = threading.Lock()

app = Flask(__name__)
CORS(app)  # 允许跨域请求

def _course_root(user):
    _, _, _, path = Create_File(str(user))
    return os.path.join(path, 'Courses')

def _course_dir(user, course_id):
    if not course_id or not re.fullmatch(r'[0-9A-Za-z_-]{8,64}', str(course_id)):
        raise ValueError('缺少或非法的 Course_Id')
    root = _course_root(user)
    path = os.path.join(root, str(course_id))
    os.makedirs(path, exist_ok=True)
    state_path = os.path.join(path, 'State.json')
    if not os.path.exists(state_path):
        with open(state_path, 'w', encoding='utf-8') as state_file:
            json.dump({}, state_file)
    return path

def _course_params(payload):
    payload = payload or {}
    return str(payload.get('User', 'Test')), payload.get('Course_Id') or payload.get('course_id')

def _course_state_path(path):
    return os.path.join(path, 'Course_Task_State.json')

def _read_course_state(path):
    state_path = _course_state_path(path)
    if not os.path.exists(state_path):
        return None
    try:
        with open(state_path, encoding='utf-8') as state_file:
            return json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return None

def _write_course_state(path, **updates):
    os.makedirs(path, exist_ok=True)
    with course_task_lock:
        state = _read_course_state(path) or {}
        state.update(updates)
        temp_path = _course_state_path(path) + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as state_file:
            json.dump(state, state_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, _course_state_path(path))
    return state

def _course_render_pipeline(path, scenes, progress_callback=None):
    """Render every course scene with one loaded TTS and SadTalker model."""
    import sys
    sys.path.insert(0, os.path.join(os.getcwd(), 'SadTalker'))
    sys.path.insert(0, os.path.join(os.getcwd(), 'VITS'))
    sys.path.insert(0, os.path.join(os.getcwd(), 'VITS', 'GPT_SoVITS'))
    from VITS.Inference import GPT_SoVITS_Model
    from SadTalker.Inference import SadTalker_Model

    path = os.path.abspath(path)
    avatar_path = os.environ.get('COURSE_AVATAR_IMAGE', '/root/autodl-tmp/inputs/Image.png')
    ref_wav_path = os.environ.get('COURSE_REF_WAV', os.path.join(os.getcwd(), 'VITS', 'Ref_Wav', 'Man.WAV'))
    if not os.path.exists(avatar_path):
        raise RuntimeError('固定数字人图片不存在，请设置 COURSE_AVATAR_IMAGE')
    if not os.path.exists(ref_wav_path):
        raise RuntimeError('固定参考音频不存在，请设置 COURSE_REF_WAV')

    render_dir = os.path.join(path, 'Course_Render')
    if os.path.exists(render_dir):
        shutil.rmtree(render_dir)
    os.makedirs(render_dir, exist_ok=True)
    vits_config = os.path.join(render_dir, 'GPT-SoVITS_config.yaml')
    sad_config = os.path.join(render_dir, 'SadTalker_config.yaml')
    shutil.copy2(os.path.join(os.getcwd(), 'VITS', 'GPT-SoVITS_config.yaml'), vits_config)
    shutil.copy2(os.path.join(os.getcwd(), 'SadTalker', 'SadTalker_config.yaml'), sad_config)
    final_video = os.path.join(path, 'last_video.mp4')

    tts = GPT_SoVITS_Model()
    tts.Initialize_Parames(vits_config)
    tts.Initialize_Models()
    sad = SadTalker_Model()
    sad.Initialize_Parames(render_dir, sad_config)
    sad.Initialize_Models()
    page_videos = []
    page_durations = []
    for index, scene in enumerate(scenes, 1):
        script = (scene.get('Script') or scene.get('Text') or '').strip()
        if not script:
            raise RuntimeError(f'第 {index} 页没有讲稿或页面文本')
        image_path = os.path.join(path, 'Course_Pages', scene.get('Image') or '')
        if not os.path.exists(image_path):
            raise RuntimeError(f'第 {index} 页图片不存在')
        audio_path = os.path.join(render_dir, f'page-{index}.wav')
        avatar_video_rel = os.path.join('Course_Render', f'page-{index}-avatar')
        avatar_mp4 = os.path.join(path, avatar_video_rel + '.mp4')
        page_video = os.path.join(render_dir, f'page-{index}.mp4')
        if progress_callback:
            progress_callback('tts', 15 + int((index - 1) * 35 / len(scenes)))
        tts.Perform_Inference(
            ref_wav_path=ref_wav_path,
            prompt_text='我制作了一站式的整合包，从训练到推理都可以零门槛上手使用',
            prompt_languageself=tts.i18n('中文'),
            target_text=script,
            target_text_language=tts.i18n('中文'),
            cut=tts.i18n('凑50字一切'),
            output_path=audio_path,
        )
        if not os.path.exists(audio_path):
            raise RuntimeError(f'第 {index} 页 GPT-SoVITS 未生成音频')
        if progress_callback:
            progress_callback('avatar', 50 + int((index - 1) * 30 / len(scenes)))
        sad.Perform_Inference(avatar_path, audio_path, os.path.join(path, avatar_video_rel))
        if not os.path.exists(avatar_mp4):
            raise RuntimeError(f'第 {index} 页 SadTalker 未生成视频')
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-loglevel', 'error', '-loop', '1', '-i', image_path, '-i', avatar_mp4,
            '-filter_complex', '[0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2[bg];[1:v]scale=360:-1[avatar];[bg][avatar]overlay=W-w-32:H-h-32:shortest=1[v]',
            '-map', '[v]', '-map', '1:a:0', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', page_video,
        ]
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', page_video]))
        page_videos.append(page_video)
        page_durations.append(duration)

    if progress_callback:
        progress_callback('merge', 90)
    concat_list = os.path.join(render_dir, 'concat.txt')
    with open(concat_list, 'w', encoding='utf-8') as concat_file:
        for page_video in page_videos:
            concat_file.write("file '" + page_video.replace("'", "'\\''") + "'\n")
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', concat_list, '-c', 'copy', final_video], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not os.path.exists(final_video):
        raise RuntimeError('FFmpeg 未生成最终视频')
    vtt_path = os.path.join(path, 'course.vtt')
    elapsed = 0.0
    with open(vtt_path, 'w', encoding='utf-8') as vtt:
        vtt.write('WEBVTT\n\n')
        for index, (scene, duration) in enumerate(zip(scenes, page_durations), 1):
            script = (scene.get('Script') or scene.get('Text') or '').strip()
            start = time.strftime('%H:%M:%S', time.gmtime(elapsed)) + f'.{int((elapsed % 1) * 1000):03d}'
            elapsed += duration
            end = time.strftime('%H:%M:%S', time.gmtime(elapsed)) + f'.{int((elapsed % 1) * 1000):03d}'
            vtt.write(f'{index}\n{start} --> {end}\n{script}\n\n')

def _course_render_task(path, task_id):
    try:
        _write_course_state(path, task_id=task_id, status='running', stage='validate', progress=5, error=None)
        scenes_path = os.path.join(path, 'Course_Scenes.json')
        if not os.path.exists(scenes_path):
            raise RuntimeError('请先解析并保存讲稿')
        with open(scenes_path, encoding='utf-8') as scenes_file:
            scenes = json.load(scenes_file)
        if not scenes:
            raise RuntimeError('课程没有可生成的场景')
        _write_course_state(path, stage='tts', progress=20)
        with course_gpu_lock:
            _write_course_state(path, stage='gpu_inference', progress=35)
            _course_render_pipeline(path, scenes, lambda stage, progress: _write_course_state(path, stage=stage, progress=progress))
        _write_course_state(path, task_id=task_id, status='success', stage='complete', progress=100, error=None)
    except Exception as exc:
        _write_course_state(path, task_id=task_id, status='failed', stage='failed', progress=100, error=str(exc))

def _safe_name(name):
    return re.sub(r'[^0-9A-Za-z._-]', '_', name or 'course')

def _pptx_text(path):
    """Extract visible text from PPTX without requiring python-pptx."""
    slides = []
    with zipfile.ZipFile(path) as archive:
        names = sorted((n for n in archive.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)), key=lambda n: int(re.search(r'\d+', n).group()))
        ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
        for name in names:
            root = ET.fromstring(archive.read(name))
            slides.append(' '.join((node.text or '').strip() for node in root.findall('.//a:t', ns) if (node.text or '').strip()))
    return slides

def _parse_course_file(source, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ext = os.path.splitext(source)[1].lower()
    pdf = os.path.join(output_dir, 'course.pdf')
    if ext != '.pdf':
        soffice = shutil.which('soffice') or shutil.which('libreoffice')
        if not soffice:
            raise RuntimeError('服务器未安装 LibreOffice')
        subprocess.run([soffice, '--headless', '--convert-to', 'pdf', '--outdir', output_dir, source], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        converted = os.path.join(output_dir, os.path.splitext(os.path.basename(source))[0] + '.pdf')
        if os.path.exists(converted):
            os.replace(converted, pdf)
    else:
        shutil.copy2(source, pdf)
    text = []
    pdftotext = shutil.which('pdftotext')
    if pdftotext:
        txt = os.path.join(output_dir, 'course.txt')
        subprocess.run([pdftotext, '-layout', pdf, txt], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(txt):
            text = [line.strip() for line in open(txt, encoding='utf-8', errors='ignore').read().split('\f')]
            while text and not text[-1]:
                text.pop()
    if not text and ext == '.pptx':
        text = _pptx_text(source)
    pdftoppm = shutil.which('pdftoppm')
    if pdftoppm:
        subprocess.run([pdftoppm, '-png', '-r', '120', pdf, os.path.join(output_dir, 'page')], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pages = sorted(n for n in os.listdir(output_dir) if n.startswith('page-') and n.endswith('.png'))
    count = max(len(pages), len(text), 1)
    return [{'Index': i + 1, 'Image': pages[i] if i < len(pages) else '', 'Text': text[i].strip() if i < len(text) else '', 'Script': ''} for i in range(count)]

@app.route('/Course_Upload', methods=['POST'])
def Course_Upload():
    user = request.form.get('User', 'Test')
    uploaded = request.files.get('File')
    if not uploaded or not uploaded.filename.lower().endswith(('.ppt', '.pptx', '.pdf')):
        return jsonify(result='Failed', message='请上传 PPT、PPTX 或 PDF'), 400
    course_id = uuid.uuid4().hex[:12]
    path = _course_dir(user, course_id)
    filename = _safe_name(uploaded.filename)
    source = os.path.join(path, 'Course_Source' + os.path.splitext(filename)[1].lower())
    uploaded.save(source)
    with open(os.path.join(path, 'Course_Meta.json'), 'w', encoding='utf-8') as f:
        json.dump({'Course_Id': course_id, 'User': str(user), 'Name': request.form.get('Course_Name', filename), 'Source': source}, f, ensure_ascii=False)
    return jsonify(result='Success', course_id=course_id)

@app.route('/Course_Parse', methods=['POST'])
def Course_Parse():
    data = request.get_json() or {}; user, course_id = _course_params(data)
    try:
        path = _course_dir(user, course_id)
    except ValueError as exc:
        return jsonify(result='Failed', message=str(exc)), 400
    meta_path = os.path.join(path, 'Course_Meta.json')
    if not os.path.exists(meta_path): return jsonify(result='Failed', message='请先上传课件'), 400
    meta = json.load(open(meta_path, encoding='utf-8'))
    try:
        scenes = _parse_course_file(meta['Source'], os.path.join(path, 'Course_Pages'))
        with open(os.path.join(path, 'Course_Scenes.json'), 'w', encoding='utf-8') as f: json.dump(scenes, f, ensure_ascii=False, indent=2)
        Task_State(path, 'Course_Parse', True)
        return jsonify(result='Success', course_id=meta['Course_Id'], scenes=scenes)
    except Exception as exc:
        Task_State(path, 'Course_Parse', False)
        return jsonify(result='Failed', message=str(exc)), 500

@app.route('/Course_Scenes', methods=['GET', 'PUT'])
def Course_Scenes():
    payload = request.args if request.method == 'GET' else (request.get_json() or {})
    user, course_id = _course_params(payload)
    try:
        path = _course_dir(user, course_id)
    except ValueError as exc:
        return jsonify(result='Failed', message=str(exc)), 400
    scenes_path = os.path.join(path, 'Course_Scenes.json')
    if request.method == 'PUT':
        scenes = (request.get_json() or {}).get('Scenes', [])
        with open(scenes_path, 'w', encoding='utf-8') as f: json.dump(scenes, f, ensure_ascii=False, indent=2)
        return jsonify(result='Success')
    if not os.path.exists(scenes_path): return jsonify(result='Failed', message='请先解析课件'), 404
    return jsonify(result='Success', scenes=json.load(open(scenes_path, encoding='utf-8')))

@app.route('/Course_Render', methods=['POST'])
def Course_Render():
    data = request.get_json() or {}; user, course_id = _course_params(data)
    try:
        path = _course_dir(user, course_id)
    except ValueError as exc:
        return jsonify(result='Failed', message=str(exc)), 400
    if not os.path.exists(os.path.join(path, 'Course_Scenes.json')): return jsonify(result='Failed', message='请先解析并保存讲稿'), 400
    current = _read_course_state(path)
    if current and current.get('status') in {'queued', 'running'}:
        return jsonify(result='Course_Render', task_id=current.get('task_id'), state=current, message='课程生成任务已在处理中'), 202
    task_id = uuid.uuid4().hex[:16]
    state = _write_course_state(path, task_id=task_id, status='queued', stage='queued', progress=0, error=None)
    executor.submit(_course_render_task, path, task_id)
    return jsonify(result='Course_Render', task_id=task_id, state=state, message='课程生成任务已创建'), 202

@app.route('/Course_State', methods=['POST'])
def Course_State():
    data = request.get_json() or {}; user, course_id = _course_params(data); task = data.get('Task', 'Course_Render')
    try:
        path = _course_dir(user, course_id)
        state = _read_course_state(path)
        if task == 'Course_Render':
            return jsonify(result=state or {'status': 'idle', 'stage': 'idle', 'progress': 0})
        return jsonify(result=Task_State(path, task))
    except ValueError as exc:
        return jsonify(result='Failed', message=str(exc)), 400
    except Exception:
        return jsonify(result=False)

@app.route('/Course_Download', methods=['GET'])
def Course_Download():
    user, course_id = _course_params(request.args)
    try:
        path = _course_dir(user, course_id)
    except ValueError as exc:
        return jsonify(result='Failed', message=str(exc)), 400
    kind = request.args.get('type', 'mp4').lower()
    filename = 'last_video.mp4' if kind == 'mp4' else ('Course_Scenes.json' if kind == 'json' else 'course.vtt')
    target = os.path.join(path, filename)
    if not os.path.exists(target): return jsonify(result='Failed', message='文件尚未生成'), 404
    return send_file(target, as_attachment=True, download_name=os.path.basename(target))

#登录
@app.route('/Login', methods=['POST'])
def Login():
    POST_JSON = request.get_json()
    user_name = POST_JSON.get("User")
    user_password = POST_JSON.get("Password")
    
    try:
        if(Verification(str(user_name), str(user_password))):
            user_result_vits_path, user_result_sadtalker_path, user_result_wav2lip_path, user_data_save_path = Create_File(str(user_name))
            Init_File(user_result_vits_path, user_result_sadtalker_path, user_result_wav2lip_path, user_data_save_path)
            return jsonify(result="Success")
        else:
            return jsonify(result="Failed")
        
    except:
        return jsonify(result="Failed")
    
#获取状态
@app.route('/Get_State', methods=['POST'])
def Get_State():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    task = POST_JSON.get('Task')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        task_state = Task_State(save_user_path,str(task))
        return jsonify(result=task_state)
    except:
        return jsonify(result="Failed")
    
# 保存PPT备注信息
@app.route('/Send_PPT_Remakes', methods=['POST'])
def Set_PPT_Remakes():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    ppt_remakes = POST_JSON.get("PPT_Remakes")

    try:
        _, _, _, save_user_path = Create_File(str(user))
        ppt_remake_filename = Save_PPT_Remake(save_user_path,ppt_remakes)
        
        with open(ppt_remake_filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        if(data != {}):
            return jsonify(result="Success")
        else:
            return jsonify(result="Failed")
    except:
        return jsonify(result="Failed")
    
#保存真人照片
@app.route('/Send_Image', methods=['POST'])
def Send_Image():
    # POST_JSON = request.get_json()
    # user = POST_JSON.get('User')
    # img = request.files.get('Image')  # 从post请求中获取图片数据
    
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    img_data_base64 = POST_JSON.get('Img')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        
        # 解码base64字符串并保存到文件
        img_data = base64.b64decode(img_data_base64)
        with open("img.png", "wb") as img_file:
            img_file.write(img_data)
        Save_Image(save_user_path,"img.png")
        
        return jsonify(result="Success")
    except:
        return jsonify(result="Failed")

#保存教师视频
@app.route('/Send_Teacher_Video', methods=['POST'])
def Send_Teacher_Video():
    string = request.form.get('Json')
    video = request.files['File'].read()
    try:
        json_data = json.loads(string)
        user = json_data.get('User')
        _, _, _, save_user_path = Create_File(str(user))
        
        # 写入文件
        with open(os.path.join(save_user_path, "Video.mp4"), "wb") as video_file:
            video_file.write(video)
        
        # 处理视频文件的保存逻辑
        # Save_Video(save_user_path, "video.mp4")
        
        return jsonify(result="Success")
    except Exception as e:
        print(e)
        return jsonify(result="Failed")
    
#获取VITS音频时长
@app.route('/Recive_Wav_Time', methods=['POST'])
def Recive_Wav_Time():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')

    try:
        result_vits_user_path, _, _, save_user_path = Create_File(str(user))
        wav_time_dict = Save_Tiem(save_user_path, result_vits_user_path)
        return  jsonify(result = wav_time_dict)
        
    except:
        return jsonify(result="Failed")
 
# 获取用户音频时长
@app.route('/Recive_User_Wav_Time', methods=['POST'])
def Recive_User_Wav_Time():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')

    try:
        _, _, _, save_user_path = Create_File(str(user))
        ppt_audio_dir = os.path.join(save_user_path, "PPT_Audio")
        audio_json_save_path = os.path.join(save_user_path, "Audio_save_path.json")
        user_wav_path = os.path.join(save_user_path, "PPT_Audio")
        
        Write_Json(ppt_audio_dir, audio_json_save_path)
        wav_time_dict = Save_Tiem(save_user_path, user_wav_path)
        return  jsonify(result = wav_time_dict)
        
    except:
        return jsonify(result="Failed")
 
#接收前端视频
@app.route('/Send_Video', methods=['POST'])
def Send_Video():
    string = request.form.get('Json')
    video = request.files['File'].read()
    
    try:
        json_data = json.loads(string)
        user = json_data.get('User')
        _, _, _, save_user_path = Create_File(str(user))
        
        # 写入文件
        with open(os.path.join(save_user_path, "PPT_Video.mp4"), "wb") as video_file:
            video_file.write(video)
        
        # 处理视频文件的保存逻辑
        # Save_Video(save_user_path, "video.mp4")
        
        return jsonify(result="Success")
    except Exception as e:
        print(e)
        return jsonify(result="Failed")

# 保存数字人插入页数的json
@app.route('/Send_People_Location', methods=['POST'])
def Send_People_Location():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    people_location = POST_JSON.get("People_Location")

    try:
        _, _, _, save_user_path = Create_File(str(user))
        people_location_filename = Save_People_Location(save_user_path,people_location)
        
        with open(people_location_filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        if(data != {}):
            return jsonify(result="Success")
        else:
            return jsonify(result="Failed")
    except:
        return jsonify(result="Failed")   

# 保存用于插入PPT的音频
@app.route('/Send_PPT_Audio', methods=['POST'])
def Send_PPT_Audio():
    string = request.form.get('Json')
    audio = request.files['File'].read()
    
    try:
        json_data = json.loads(string)
        user = json_data.get('User')
        audio_name = json_data.get('Audio_Name')
        
        _, _, _, save_user_path = Create_File(str(user))
        Save_Insert_Audio(save_user_path, audio_name, audio)
        
        return jsonify(result="Success")
    except:
        return jsonify(result="Failed")

#####################################################################################
#                                 配置参数                                           #
#####################################################################################

# 配置所有模型参数
@app.route('/Send_Config', methods=['POST'])
def Send_Config():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    vits_config = POST_JSON.get('VITS_Config')
    sadtalker_config = POST_JSON.get('SadTalker_Config')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        
        Config_SadTalker_Parmes(save_user_path, sadtalker_config)
        Config_VITS_Parmes(save_user_path, vits_config)
        
        return jsonify(result="Success")
    except:
        return jsonify(result="Failed")
    
    
#配置wav2lip参数
@app.route('/Send_Wav2Lip_Config', methods=['POST'])
def Send_Wav2Lip_Config():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    wav2lip_config = POST_JSON.get('Wav2Lip_Config')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        
        Config_Wav2Lip_Parmes(save_user_path, wav2lip_config)
        
        return jsonify(result="Success")
    except:
        return jsonify(result="Failed")
    
#选择训练的VITS模型
@app.route('/Send_Select_Train_VITS_Model', methods=['POST'])
def Send_Select_Train_VITS_Model():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        Select_Train_VITS_Model(save_user_path,user)
        
        return jsonify(result="Success")
    
    except:
        return jsonify(result="Failed")

#选择VITS模型
@app.route('/Send_Select_VITS_Model', methods=['POST'])
def Send_Select_VITS_Model():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    index = POST_JSON.get('Index')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        
        Select_VITS_Model(save_user_path,str(index))
        
        return jsonify(result="Success")
    except:
        return jsonify(result="Failed")
   
    
#####################################################################################
#                                VITS功能                                           #
#####################################################################################

#保存用于训练VITS的音频
@app.route('/Send_Tarin_Audio', methods=['POST'])
def Send_Tarin_Audio():
    string = request.form.get('Json')
    audio = request.files['File'].read()
    
    try:
        json_data = json.loads(string)
        user = json_data.get('User')
        audio_name = json_data.get('Audio_Name')
        
        _, _, _, save_user_path = Create_File(str(user))
        Save_Train_Audio(save_user_path, audio_name, audio)
        
        return jsonify(result="Success")
    except:
        return jsonify(result="Failed")

#训练VITS模型
@app.route('/Train_VITS_Model', methods=['POST'])
def Train_VITS_Model():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    json_data = POST_JSON.get('Label')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        Task_State(save_user_path, "VITS_Train", False)
        executor.submit(Train_VITS, save_user_path, user, json_data)

        return jsonify(result="VITS_Train")
    except:
        return jsonify(result="Failed")
    
#保存VITS的参照音频跟文字
@app.route('/Send_Ref_Wav_And_Text', methods=['POST'])
def Send_Ref_Wav_And_Text():
    string = request.form.get('Json')
    audio = request.files['File'].read()
    
    try:
        json_data = json.loads(string)
        user = json_data.get('User')
        ref_text = json_data.get('Ref_Text')
        _, _, _, save_user_path = Create_File(user)
        
        file =  save_user_path + "/" + "Audio.mp3"
         # 写入文件
        with open(file, "wb") as audio_file:
            audio_file.write(audio)
            
        Save_VITS_Ref_Wav_And_Text(save_user_path, file,  {"Text" : ref_text}, "None")
            
        return jsonify(result="Success")
    except Exception as e:
        print(e)
        return jsonify(result="Failed")
    
#获取训练后的VITS模型的名字
@app.route('/Get_Train_VITS_Model_Name', methods=['POST'])
def Get_Train_VITS_Model_Name():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    
    try:
        _, _, _, save_user_path = Create_File(str(user))
        weightPath = os.path.join(save_user_path, "Weight")
        #判断文件夹不为空
        if len(os.listdir(weightPath)) > 0:
            return jsonify(result=f"{user}")
        else:
            return jsonify(result="Failed")
        
    except:
        return jsonify(result="Failed")
    
#####################################################################################
#                                      推理                                         #
#####################################################################################
   
#推理效果展示视频
@app.route('/Get_Test_Inference', methods=['POST'])
def Get_Test_Inference():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        result_vits_user_path, result_sadtalker_user_path, _, save_user_path = Create_File(user)
        DH = VITS_Sadtalker_Join(result_vits_user_path, result_sadtalker_user_path, save_user_path)
        sad_parames_yaml_path, vits_parames_yaml_path, _ = Get_Parmes(save_user_path)
        DH.Set_Params_and_Model(sad_parames_yaml_path, vits_parames_yaml_path)
        
        with open(os.path.join(save_user_path,"Ref_text.json"), "r", encoding='utf-8') as f:
            data = json.load(f)
            
        ref_wav_path = os.path.join(save_user_path,"Ref_Wav.wav")
        ref_text = data["Text"]
        test = "你好，我是数字人授课录制系统，很高兴为您服务。"
        video_output_path = os.path.join(save_user_path,"Test")
        imag_path = os.path.join(save_user_path,"Image.png")
        
        audio_path = DH.Inference_VITS_test(ref_wav_path, ref_text, test)
        DH.Inference_SadTalker_test(imag_path, audio_path, video_output_path)
        
        video_data_base64 = Encode_Video(os.path.join(save_user_path,"Test.mp4"))
        
        return jsonify(result = video_data_base64)
    except:
        return jsonify(result="Failed")
       
#推理VITS(单个)
@app.route('/Get_Inference_VITS', methods=['POST'])
def Get_Inference_VITS():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        result_vits_user_path, result_sadtalker_user_path, _, save_user_path = Create_File(user)
        DH = VITS_Sadtalker_Join(result_vits_user_path, result_sadtalker_user_path, save_user_path)
        sad_parames_yaml_path, vits_parames_yaml_path, _ = Get_Parmes(save_user_path)
        DH.Set_Params_and_Model(sad_parames_yaml_path, vits_parames_yaml_path)
        
        with open(os.path.join(save_user_path,"Ref_text.json"), "r", encoding='utf-8') as f:
            data = json.load(f)
        
        ref_wav_path = os.path.join(save_user_path,"Ref_Wav.wav")
        ref_text = data["Text"]
        test = "你好，我是数字人授课录制系统，很高兴为您服务。"

        
        DH.Inference_VITS_test(ref_wav_path, ref_text, test)
        
        
        return jsonify(result="Success")
    except:
        return jsonify(result="Failed")
      
#推理VITS(多个)
@app.route('/Get_Inference_VITS_Multiple', methods=['POST'])
def Get_Inference_VITS_Multiple():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        result_vits_user_path, result_sadtalker_user_path, _, save_user_path = Create_File(user)
        Task_State(save_user_path, "VITS_Inference", False)
        # 将任务提交到线程池 
        # executor.submit(VITS_Multiple_Inference, result_vits_user_path, result_sadtalker_user_path, save_user_path)
        VITS_Multiple_Inference(result_vits_user_path, result_sadtalker_user_path, save_user_path)
        return jsonify(result="VITS_Inference")
    except:
        return jsonify(result="Failed")
      
# 推理VITS跟Sadtalker
@app.route('/Get_Inference_VITS_Sadtalker', methods=['POST'])
def Get_Inference_VITS_Sadtalker():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        result_vits_user_path, result_sadtalker_user_path, _, save_user_path = Create_File(user)
        Task_State(save_user_path, "Audio_Video_Inference", False)
        # 将任务提交到线程池 
        executor.submit(VITS_Sadtalker_Inference, result_vits_user_path, result_sadtalker_user_path, save_user_path)

        return jsonify(result="Audio_Video_Inference")
    except:
        return jsonify(result="Failed")
    
# 推理用户音频跟Sadtalker
@app.route('/Get_Inference_User_Audio_Sadtalker', methods=['POST'])
def Get_Inference_User_Audio_Sadtalker():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        _, result_sadtalker_user_path, _, save_user_path = Create_File(user)
        Task_State(save_user_path, "Audio_Video_Inference", False)
        # 将任务提交到线程池 
        executor.submit(User_Wav_Sadtalker_Inference, result_sadtalker_user_path, save_user_path)

        return jsonify(result="Audio_Video_Inference")
    except:
        return jsonify(result="Failed")
    
    
# 推理VITS跟Wav2Lip
@app.route('/Get_Inference_VITS_Wav2Lip', methods=['POST'])
def Get_Inference_VITS_Wav2Lip():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    
    result_vits_user_path, _, result_wav2lip_user_path, save_user_path = Create_File(user)
    Task_State(save_user_path, "Audio_Video_Inference", False)
    VITS_Wav2Lip_Inference(result_vits_user_path, result_wav2lip_user_path, save_user_path)
    # 将任务提交到线程池 
    # executor.submit(VITS_Wav2Lip_Inference, result_vits_user_path, result_wav2lip_user_path, save_user_path)
    
    return jsonify(result="Audio_Video_Inference")

# 推理用户音频跟Wav2Lip
@app.route('/Get_Inference_User_Audio_Wav2Lip', methods=['POST'])
def Get_Inference_User_Audio_Wav2Lip():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    
    _, _, result_wav2lip_user_path, save_user_path = Create_File(user)
    Task_State(save_user_path, "Audio_Video_Inference", False)
    # 将任务提交到线程池 
    executor.submit(User_Wav_Wav2Lip_Inference, result_wav2lip_user_path, save_user_path)
    
    return jsonify(result="Audio_Video_Inference")

# ppt跟视频合成（合成最终效果视频，全插入数字人） 
@app.route('/PPT_Video_Merge', methods=['POST'])
def PPT_Video_Merge():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        _, _, _, save_user_path = Create_File(user)
        # 将任务提交到线程池 
        Task_State(save_user_path, "Video_Merge", False)
        executor.submit(Video_Merge,save_user_path)
        
        return jsonify(result = "Video_Merge")
    except:
        return jsonify(result="Failed")

# ppt跟视频合成（合成最终效果视频，可选择插入数字人）
@app.route('/PPT_Video_Merge_Select_Into', methods=['POST'])
def PPT_Video_Merge_Select_Into():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        _, _, _, save_user_path = Create_File(user)
                # 将任务提交到线程池 
        Task_State(save_user_path, "Video_Merge", False)
        executor.submit(Video_Merge_Select_Into,save_user_path)
        
        return jsonify(result = "Video_Merge")
    except:
        return jsonify(result="Failed")
        
# ppt跟视频合成（没有数字人）
@app.route('/PPT_Video_Merge_No_Into', methods=['POST'])
def PPT_Video_Merge_No_Into():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        _, _, _, save_user_path = Create_File(user)
        Video_Join_Audio(save_user_path)
        
        return jsonify(result = "Success")
    except:
        return jsonify(result="Failed")

#####################################################################################
#                                    获取                                           #
#####################################################################################

# 拉取视频
@app.route('/Pull_Video_Merge', methods=['POST'])
def Pull_Video_merge():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        _, _, _, save_user_path = Create_File(user)
        last_video = os.path.join(save_user_path,"last_video.mp4")
        video_data_base64 = Encode_Video(last_video)
        return jsonify(result = video_data_base64)
    except:
        return jsonify(result="Failed")
 
 
# 拉取推理的VITS声音
@app.route('/Pull_VITS_Audio', methods=['POST'])
def Pull_VITS_Audio():
    POST_JSON = request.get_json()
    user = POST_JSON.get('User')
    try:
        _, _, _, save_user_path = Create_File(user)
        vits_wav = os.path.join(save_user_path,"Test_VITS.wav")
        wav_data_base64 = Encode_Video(vits_wav)
        return jsonify(result=wav_data_base64)
    except:
        return jsonify(result="Failed")
 
 
    
if __name__ == '__main__':
    
    app.run("0.0.0.0")


