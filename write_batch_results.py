#!/usr/bin/env python3
"""Write per-batch result.json for 配对机_20261027 run (LLM process-analysis batches).
No GT access. Every step references in-batch candidates; every event is attached
to a step or recorded in unresolved_events with an honest reason.
"""
import csv, json, re, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
RUN = Path(r'outputs\配对机_20261027')

def norm(s):
    return re.sub(r'\s+', '', str(s or '')).lower()

# ---------------- classification decision table ----------------
def F(ptype, obj, start, end, craft, boundary, note=''):
    return {'过程类型': ptype, '统计对象': obj, '起点含义': start, '终点含义': end,
            '工艺依据': craft, '边界依据': boundary, '说明': note}

ST_EXIT_TMPL = ('候选{cid}为状态退出观察（{state}；边界规范化状态={bstat}），'
                '后继状态未解析，全局退出等价未证明，不能作为已验证计时区间')
AE_TMPL = ('候选{cid}为动作/功能观察（{act}；边界规范化状态={bstat}），缺结束标签，'
           '不能构成起止标签对，仅保留动作观察记录')
WIRED_TMPL = ('候选{cid}为源码迁移证据（{state}；边界规范化状态={bstat}），原门控：{cond}')

def st_exit_boundary(r):
    return ST_EXIT_TMPL.format(cid=r['候选ID'], state=r['状态'], bstat=r['边界规范化状态'])

def ae_boundary(r):
    return AE_TMPL.format(cid=r['候选ID'], act=r['动作'], bstat=r['边界规范化状态'])

def wired_boundary(r):
    return WIRED_TMPL.format(cid=r['候选ID'], state=r['状态'], bstat=r['边界规范化状态'],
                             cond=(r['原始条件'] or '门控见源码迁移边')[:160])

def cls_iStage(r):
    return F('交接数据', '电能表数据采集功能块FB_EnergyMeter的UDP通讯状态机（iStage）本次退出阶段',
             'iStage进入该状态值，开始本阶段通讯/处理动作（连接、清缓存、发送、接收或解析）',
             'iStage离开该状态值（收到fbSkt*.Done/Error步进，或连接断开时复位0）',
             'FB_EnergyMeter::ST内CASE iStage各分支调用SktUDPCreate/SktUDPSend/SktUDPRcv/SktClose并按Done/Error步进（源码注释：命令时序/实时读取）',
             st_exit_boundary(r),
             '通讯数据采集阶段；ST体含数组元素赋值被解析器记为unsupported_ST，各状态值具体动作与后继待人工核验')

def cls_QZTJ(r):
    return F('内部辅助', 'AM强制停机点检弹窗管理（QZTJDJ_Code弹窗状态机）本次状态阶段',
             'QZTJDJ_Code进入该弹窗状态值，执行对应弹窗/报警置位或复位',
             'QZTJDJ_Code退出该状态值（切换到其他弹窗状态或复位0）',
             'CATL_ZHTJ_FB::body按QZTJDJ_Code分支置位/复位ZHTJ_Window[4..7]、QZTJDJ_EQ_*_Memory与报警（源码注释：AM强制停机点检弹窗&报警触发/复位）',
             st_exit_boundary(r), '弹窗/报警管理为内部辅助，不属于工艺计时过程')

def cls_ZHTJ(r):
    return F('内部辅助', '智慧停机弹窗与灯光管理（ZHTJ_Code弹窗状态机）本次状态阶段',
             'ZHTJ_Code进入该状态值，执行TOM/MES/EPM弹窗置位、灯光输出或复位',
             'ZHTJ_Code退出该状态值（切换状态或复位0）',
             'CATL_ZHTJ_FB::body按ZHTJ_Code分支操作LightWarming、TOM_StopWindow、MES_LockWindow、ZHTJ_Window/Alarm/EQ_*_Memory（源码注释：智慧停机灯光要求/TOM/MES/EPM弹窗）',
             st_exit_boundary(r), '弹窗/灯光管理为内部辅助，不属于工艺计时过程')

def cls_DateTime(r):
    return F('周期汇总', '生产统计早班清零时刻观察（系统时间DateTime字段）',
             '系统时间到达观察值（如8点/20点整、分=0、秒=0）',
             '时间字段离开该值（进位），清零触发窗口结束',
             'UN_ControlStateMachine::SC03_HMIInterface源码注释：生产统计，需要供应商链接生产总数和NG数量（注意在早班时间将设备数据清零）',
             st_exit_boundary(r), '时刻触发型周期汇总观察，非持续工艺过程，不建议作为计时区间')

def cls_HMIStat(r):
    return F('周期汇总', '生产统计时长累加器进位观察（自动/开机/打烊/停机/等待时间）',
             '累加器字段到达观察值（秒0/6、分0/60）',
             '字段离开该值（进位），本观察窗口结束',
             'UN_ControlStateMachine::SC03_HMIInterface生产统计注释；Machine*Time累加器按秒/分进位',
             st_exit_boundary(r), '统计累加器进位观察，不建议作为工艺计时区间')

def cls_MachTime(r):
    return F('周期汇总', '生产统计时长累计值观察（MachineAuotTime/OpenTime/PuttyTime/StopTime/WaitTime）',
             '累计值到达观察值（0或10）',
             '累计值离开该值（清零或进位）',
             'UN_ControlStateMachine::SC03_HMIInterface生产统计注释；各Machine*Time累计清零/进位',
             st_exit_boundary(r), '统计值观察，不建议作为工艺计时区间')

def cls_PackStat(r):
    return F('周期汇总', 'TOM机器状态镜像值观察（PackTags.Status.MachineStatus=45/58/84/100）',
             'PackTags.Status.MachineStatus进入该状态值',
             '该状态值退出（切换到其他TOM状态）',
             'UN_ControlStateMachine::SC03_HMIInterface生产统计注释（统计设备处于各TOM状态的时间）；PrChectout_Station_full注释：计算设备TOM系统中处于100状态的时间',
             st_exit_boundary(r), '状态镜像值退出观察，是统计输入而非独立工艺过程')

def cls_PackMode(r):
    return F('内部辅助', 'PackML模式镜像（PackTags.Status.UnitModeCurrent=1/2/3）',
             'UnitModeCurrent进入该模式值（生产/维护/手动）',
             '该模式值退出，镜像线圈写PackMLMode[0]对应位结束',
             'UN_ControlStateMachine::SC03_HMIInterface按UnitModeCurrent写PackMLMode[0].Production/Maintenace/Manual（源码注释：模式定义为位）',
             st_exit_boundary(r), '模式镜像辅助逻辑')

def cls_MachStatus(r):
    return F('内部辅助', '固定LOSS报警停机状态输出与复位（MachineStatus=45/58/84/100）',
             'MachineStatus进入该状态值，执行45状态输出或LOSS报警复位',
             'MachineStatus退出该状态值',
             'HMI_Confirm_FC::body在MachineStatus=45时输出报警停机状态、=58/84/100时Clear HMI_LOSS_Alarm*/LOSSAlarm_Te*/LossID_Upload*（源码注释）',
             st_exit_boundary(r), '报警弹窗/状态管理内部辅助')

def cls_FirstAlarm(r):
    return F('内部辅助', '首报警ID记录（FirstAlarmID）',
             'FirstAlarmID处于观察值（0=无首报警）',
             'FirstAlarmID离开该值（记录首报警或被复位）',
             'HMI_Confirm_FC::body首报警ID记录与TON延时观察',
             st_exit_boundary(r) if r['状态'] == 'state_exit' else ae_boundary(r),
             '报警管理内部辅助')

def cls_UnitMode(r):
    return F('内部辅助', '设备自动运行标志（UnitModeCurrent=1）',
             'UnitModeCurrent进入1（设备自动运行中）',
             '退出1，写Auto_Run线圈动作结束',
             'HMI_Confirm_FC::body按UnitModeCurrent=1写Auto_Run（源码注释：设备自动运行中）',
             st_exit_boundary(r) if r['状态'] == 'state_exit' else ae_boundary(r), '运行标志辅助')

def cls_ModeSet(r):
    return F('内部辅助', '设备模式设置状态（iSys_ModeSet=0/1/2）',
             'iSys_ModeSet进入该模式值',
             'iSys_ModeSet退出该值（模式切换）',
             'EM00_MachineControl::SC01_Main模式设置逻辑',
             st_exit_boundary(r), '模式设置辅助')

def cls_iCmdNo(r):
    em = r['构成要素'].split('.')[0]
    return F('内部辅助', f'{em}模块PackML命令号（iCmdNo=1切换命令）状态退出',
             f'{em}.ProcessCtrl.iCmdNo进入1（切换命令有效）',
             'iCmdNo退出1（命令被清除或模块内部状态机改写）',
             'EM00_MachineControl::SC01_Main对各EM模块发PackML切换命令；EM模块内部状态机为外部功能块，不在本工程ST/LD源码中',
             st_exit_boundary(r), 'EM模块内部状态机不可见，后继未知，仅保留命令号退出观察')

def cls_AxisPar(r):
    return F('内部辅助', '轴驱动参数值观察（' + r['构成要素'] + '）',
             '参数值处于观察值（来自HMI/配方的速度、加减速度、回零参数等）',
             '参数值离开该值（被重新写入）',
             'FB_RmDriver::ST/FB_RmDriver_ND::ST参数赋值逻辑；参数写入为配置行为',
             st_exit_boundary(r), '参数值观察，不是运动过程，不建议作为计时区间')

def cls_FaultCode(r):
    return F('检测测量', '轴驱动故障码检测（stRmAxisPdo.In.diFaultCode=1..6）',
             '故障码进入该值（检测到对应故障类别）',
             '故障码离开该值（故障清除或变化）',
             'FB_RmDriver_ND::ST读取stRmAxisPdo.In.diFaultCode并分类',
             st_exit_boundary(r), '故障检测观察；持续时间语义取决于故障保持时长，待人工核验')

def cls_Bcr(r):
    name = r['构成要素']
    sub = {'iBcr_Cmd': '扫码命令状态机（iBcr_Cmd）',
           'iBcr_HcsStatus': '扫码器连接状态（iBcr_HcsStatus）',
           'iBcr_Rsp': '扫码应答码状态机（iBcr_Rsp）'}.get(name, name)
    return F('交接数据', '条码扫描器通讯（FB_Scan_New）' + sub + '本次阶段',
             '该字段进入该状态值，发出扫码命令/等待应答/处理结果',
             '该字段退出该值（命令完成或状态步进）',
             'FB_Scan_New::ST条码扫码器命令/连接/应答状态机',
             st_exit_boundary(r), '扫码数据交接阶段，后继未解析，待人工核验')

def cls_BcrAlarm(r):
    return F('内部辅助', '扫码连续NG报警次数设定（stScan.iBCRConNGTimesAlarmSet）',
             '设定值处于观察值（0或3）',
             '设定值离开该值（被重新设置）',
             'FB_Scan_New::ST连续NG报警设定逻辑',
             st_exit_boundary(r), '设定值观察')

def cls_NGCount(r):
    return F('周期汇总', '当日NG数量统计（stNgCount.today.arNgNumber[i]）',
             '该时段NG计数值处于观察值',
             '计数值离开该值（清零或累加）',
             'FB_NGCount_Hour::ST按时段统计当日NG数量',
             st_exit_boundary(r), '计数统计观察，非工艺计时过程')

def cls_Shift(r):
    return F('周期汇总', '班次/日期切换统计（' + r['构成要素'] + '）',
             '班次/日期字段进入该值',
             '字段离开该值（换班/换日）',
             'FB_NGCount_Hour::ST班次切换与隔班清零逻辑',
             st_exit_boundary(r), '换班统计观察')

def cls_PCComm(r):
    return F('交接数据', 'PLC与上位PC通讯握手状态机（PLC_PC_Communication的' + r['构成要素'] + '）',
             '该字段进入该状态值，执行发送/接收/应答握手',
             '该字段退出该值（握手步进或复位）',
             'PLC_PC_Communication::body通讯握手逻辑；InteractionStep按Out/Receive条件步进（源码迁移T0000001门控：Out=1 AND NOT(Trig) AND InteractionStep=10 AND Receive∈{1,2,3}）',
             st_exit_boundary(r) if r['状态'] == 'state_exit' else wired_boundary(r),
             '通讯握手数据交接；C0000350有完整源码门控，其余后继待人工核验')

def cls_Login(r):
    return F('内部辅助', 'HMI刷卡登录状态（' + r['构成要素'] + '）',
             '登录状态字段进入该值（权限级别/登录步骤）',
             '字段退出该值（登出或步骤切换）',
             'Parmmeters_Production::Keyboard刷卡登录逻辑（源码注释：HMI刷卡登录）',
             st_exit_boundary(r), '登录管理辅助')

def cls_MesProc(r):
    return F('内部辅助', 'MES流程下载计数（stMesProc.ar_iProcDown[458]）',
             '计数值处于观察值（10）',
             '计数值离开该值',
             'Parmmeters_Production::Keyboard MES流程下载计数（源码注释：HMI刷卡登录）',
             st_exit_boundary(r), '计数观察')

def cls_Mes(r):
    name = r['构成要素']
    sub = {'iProcDown': 'MES下料流程（iProcDown）',
           'iProcUp': 'MES上料流程（iProcUp）',
           'iProcResult': 'MES处理结果（iProcResult）'}.get(name, name)
    return F('交接数据', 'MES上/下料数据流程（FB_Mes）' + sub + '本次阶段',
             '该字段进入该状态值，执行MES请求/上传/结果处理',
             '该字段退出该值（请求完成或失败分支）',
             'FB_Mes::ST的MES上/下料流程状态机',
             st_exit_boundary(r), 'MES数据交接阶段，后继未解析，待人工核验')

def cls_OutStation(r):
    name = r['构成要素']
    sub = {'iMes_Rs': '出料站MES请求状态（iMes_Rs）',
           'iMes_Rsp': '出料站MES应答状态（iMes_Rsp）'}.get(name, name)
    return F('交接数据', '出料站MES交互（FB_OutStation）' + sub + '本次阶段',
             '该字段进入该状态值，发起请求/等待应答/处理结果',
             '该字段退出该值',
             'FB_OutStation::ST出料站MES请求/应答状态机',
             st_exit_boundary(r), '出料站数据交接阶段，待人工核验')

def cls_NGAlarmSet(r):
    return F('内部辅助', 'MES NG次数报警设定（stMes.iNGTimesAlarmSet）',
             '设定值处于观察值（0）',
             '设定值离开该值',
             'FB_OutStation::ST的NG次数报警设定',
             st_exit_boundary(r), '设定值观察')

def cls_Cylinder(r):
    return F('内部辅助', '气缸功能块参数（FB_Gripper_YV的' + r['构成要素'] + '）',
             '参数值处于观察值（前后到位延时0/5、超时0/3000、传感器模式0）',
             '参数值离开该值（被重新设置）',
             'FB_Gripper_YV::ST气缸参数赋值逻辑；为到位延时/超时配置而非气缸动作本身',
             st_exit_boundary(r), '气缸参数观察，不是机械动作过程')

def cls_AD(r):
    return F('检测测量', '模拟量上下限设定（FB_AD的' + r['构成要素'] + '）',
             '上下限值处于观察值（rAnaHi=0/8500、rAnaLw=-100/0）',
             '上下限值离开该值（被重新设置）',
             'FB_AD::ST模拟量采集上下限设定逻辑',
             st_exit_boundary(r), '检测设定值观察，非检测动作本身')

def cls_SensorIn(r):
    return F('检测测量', '传感器计数输入（FB_SensorCount的' + r['构成要素'] + '）',
             '输入处于观察值（1=传感器有效）',
             '输入离开该值（传感器释放）',
             'FB_SensorCount::ST对IN[i]/IN[n]/IN[p]输入计数',
             st_exit_boundary(r), '传感器脉冲观察；单次脉冲宽度即观察时长，待人工核验')

def cls_Avoid(r):
    return F('内部辅助', '避让/气缸方式选择（iAvoidOrCylinder=1/2）',
             '选择值处于该值',
             '选择值离开该值（方式切换）',
             'FB_SensorCount::ST避让/气缸方式选择',
             st_exit_boundary(r), '方式选择辅助')

def cls_CCD(r):
    name = r['构成要素']
    sub = {'IN_Done1': 'CCD检测完成1（IN_Done1）',
           'IN_Done2': 'CCD检测完成2（IN_Done2）',
           'IN_Ready': 'CCD检测就绪（IN_Ready）'}.get(name, name)
    return F('检测测量', 'CCD视觉检测（FB_CCD_CIML）' + sub,
             '信号进入该值（0=无效，1=就绪/完成）',
             '信号离开该值（检测结束或复位）',
             'FB_CCD_CIML::ST CCD检测就绪/完成信号处理',
             st_exit_boundary(r), '检测信号观察；检测保持时长即信号保持期，待人工核验')

def cls_MachCode(r):
    return F('内部辅助', '机型代码选择（iMachineCode=1..8，VersionRecord）',
             '机型代码处于该值',
             '机型代码离开该值（机型切换）',
             'VersionRecord机型记录逻辑',
             st_exit_boundary(r), '机型配置辅助')

def cls_iMode(r):
    return F('内部辅助', '模式选择（iMode=0..4）',
             '模式字段处于该值',
             '模式字段离开该值（模式切换）',
             '源码模式选择逻辑（POU：' + r['证据POU'] + '）',
             st_exit_boundary(r), '模式选择辅助')

def cls_AntiStep(r):
    return F('内部辅助', '伺服防碰撞步骤状态（FB_ServoAnticollision的iStep）',
             'iStep进入该步骤值（0待机/10/20执行）',
             'iStep退出该值（步骤完成或超时）',
             'FB_ServoAnticollision::ST防碰撞步骤逻辑',
             st_exit_boundary(r), '防碰撞辅助步骤，后继未解析，待人工核验')

def cls_AntiTimeout(r):
    return F('内部辅助', '伺服防碰撞超时参数（uiTimeOut=0/300）',
             '超时参数处于该值',
             '超时参数离开该值（被重新设置）',
             'FB_ServoAnticollision::ST超时参数赋值',
             st_exit_boundary(r), '参数观察')

def cls_StationChoose(r):
    return F('内部辅助', '转向轴组工位选择（iStationchoose=0/1）',
             '工位选择处于该值',
             '工位选择离开该值（切换）',
             'FB_TurnAxisGroup::ST工位选择逻辑',
             st_exit_boundary(r), '工位选择辅助')

def cls_Velocity(r):
    return F('内部辅助', '轴速度设定（MC_Axis_FB的' + r['构成要素'] + '）',
             '速度值处于观察值（0）',
             '速度值离开该值（被设置）',
             'MC_Axis_FB轴速度设定（源码注释：自动速度/手动）',
             st_exit_boundary(r), '速度设定值观察')

def cls_ModulExit(r):
    return F('内部辅助', '轴定位模式选择（Modul=0绝对/1相对）',
             '模式字段处于该值',
             '模式字段离开该值（模式切换）',
             'MC_Axis_FB按Modul区分绝对/相对运行（源码注释：绝对运行/相对运行）',
             st_exit_boundary(r), '模式选择辅助；完成线圈观察另见机械动作候选')

def cls_ModulCoil(r):
    return F('机械动作', '轴定位运行完成信号（' + ('绝对运行' if '0' in r['开始标签'] else '相对运行') + '）',
             '定位模式处于该模式且完成线圈被写（AbsoluteDone/RelativeDone）',
             '未采集到结束边界（线圈观察无状态终点）',
             'MC_Axis_FB按Modul写AbsoluteDone/RelativeDone完成信号（源码注释：绝对运行/相对运行）',
             ae_boundary(r), '轴运动相关动作观察；缺结束边界，不能构成起止标签对，待人工核验')

def cls_TOM(r):
    return F('内部辅助', 'TOM事件缓冲管理（' + r['构成要素'] + '）',
             '缓冲/触发字段处于观察值',
             '字段离开该值（缓冲清空或触发切换）',
             'TOM_EventsManagement_FB::ST事件缓冲与触发管理',
             st_exit_boundary(r), '事件管理辅助')

def cls_Event(r):
    return F('内部辅助', '首报警记录（' + r['构成要素'] + '）',
             '首报警字段处于观察值（FirstOutAlarm=0、Msg_Category=10/20）',
             '字段离开该值（记录或清除）',
             'Event_FC::ST首报警与报警类别管理',
             st_exit_boundary(r), '报警管理辅助')

def cls_GL20(r):
    if r['构成要素'] == '节点号':
        return F('内部辅助', 'GL20模块节点号（节点号=0）',
                 '节点号处于观察值',
                 '节点号离开该值（配置变化）',
                 'SET_GL20_8AD::ST模块配置',
                 st_exit_boundary(r), '模块配置辅助')
    return F('检测测量', 'GL20模拟量模块数据读取（ReadDate_0=0/1）',
             '读取状态处于该值',
             '读取状态离开该值（读取完成或停止）',
             'SET_GL20_8AD::ST模拟量数据读取控制',
             st_exit_boundary(r), '数据读取状态观察；名称含义待人工确认')

def cls_Language(r):
    return F('内部辅助', '语言选择记录（iLanguage=0/1/2）',
             '语言字段处于该值',
             '语言字段离开该值（语言切换）',
             'F_ConRecord::ST语言记录',
             st_exit_boundary(r), '界面配置辅助')

def cls_Magnetic(r):
    return F('内部辅助', '磁轨道参数计算值（F_CalculateRatio的' + r['构成要素'] + '）',
             '参数值处于观察值（0）',
             '参数值离开该值（重新计算写入）',
             'F_CalculateRatio::ST磁轨道长度/宽度/弧度计算',
             st_exit_boundary(r), '计算参数观察')

def cls_Mover(r):
    return F('内部辅助', '动子存储校验（F_MoverMemoryCheck的' + r['构成要素'] + '）',
             '动子ID/区域计数字段处于观察值（MovID=252、区域计数=4）',
             '字段离开该值（校验通过或改写）',
             'F_MoverMemoryCheck::ST动子存储校验',
             st_exit_boundary(r), '动子管理辅助')

def cls_CT(r):
    return F('内部辅助', 'CT功能块内部步骤（' + r['构成要素'] + '）',
             '步骤/计数字段处于观察值',
             '字段离开该值（步进）',
             'FB_CT::ST内部步骤计数',
             st_exit_boundary(r), '内部步骤辅助')

def cls_PR(r):
    return F('周期汇总', '隔班PR数据清零（PR_Date.Sec=0触发PR_Reset）',
             '到达清零时刻条件（PR_Date.Sec=0）',
             '清零动作完成/条件离开（PR_Reset线圈观察缺结束边界）',
             'PrChectout_Station_full::body源码注释：隔班数据清零及保存当天PR数据',
             st_exit_boundary(r) if r['状态'] == 'state_exit' else ae_boundary(r),
             '隔班清零属周期汇总；线圈行缺结束边界')

def cls_MachineState(r):
    return F('周期汇总', 'TOM 100状态时间统计输入（MachineState=45/58/84/100）',
             'MachineState进入该状态值',
             'MachineState退出该值',
             'PrChectout_Station_full::body源码注释：计算设备TOM系统中处于100状态的时间',
             st_exit_boundary(r), '状态时间统计输入观察')

def cls_Recipe(r):
    return F('交接数据', '配方数据读取（stRecipeCtrl.stRecipeCur.ar_rData[' + r['构成要素'][-3:-1] + '].bit1）',
             '配方读取完成位处于1（该字段读取完成）',
             '该位离开1（下一次读取或清除）',
             'Parmmeters_Production::SC01_Recipe源码注释：配方数据读取',
             st_exit_boundary(r), '配方数据交接观察，待人工核验')

def cls_Edge(r):
    return F('内部辅助', '边沿/停止命令检测辅助（' + r['构成要素'][:40] + '）',
             '辅助表达式成立（边沿或命令组合检测）',
             '表达式不成立（边沿结束）',
             'VersionRecord/相关ST体内的边沿检测与停止命令记忆逻辑（Q/bStopLast赋值）',
             st_exit_boundary(r), '边沿检测辅助变量观察')

def cls_LoopVar(r):
    return F('内部辅助', '循环变量/位置ID辅助（' + r['构成要素'] + '，FB_RmDriver_ND）',
             '辅助变量处于观察值（i=0/1、iPosId=-1/0）',
             '变量离开该值（循环步进/查找完成）',
             'FB_RmDriver_ND::ST循环与位置查找逻辑',
             st_exit_boundary(r), '内部循环辅助')

def cls_5min(r):
    return F('内部辅助', '5分钟时钟脉冲CTU计数（temp5minCTU_CV=5）',
             'CTU计数值到达5（1秒脉冲×5）',
             '计数值离开5（复位后重新计数）',
             'UN_ControlStateMachine相关网络：CTU对1s脉冲计数产生t5minClk分钟级脉冲',
             st_exit_boundary(r) if r['状态'] == 'state_exit' else ae_boundary(r),
             '周期脉冲辅助，用于弹窗重复触发等')

def cls_FirstRun(r):
    return F('内部辅助', 'UDP通讯初始化首扫描标志（bFirstRun）',
             'bFirstRun为TRUE（首扫描待初始化）',
             '未采集到结束边界（赋值观察bFirstRun:=FALSE无状态终点）',
             'VersionRecord::ST/相关ST体首扫描初始化赋值',
             ae_boundary(r), '初始化辅助标志；缺结束边界')

def cls_default(r):
    return F('内部辅助', '内部辅助变量观察（' + r['构成要素'] + '）',
             '变量处于观察值',
             '变量离开该值',
             '源码POU：' + r['证据POU'] + '；动作：' + r['动作'][:60],
             st_exit_boundary(r) if r['状态'] == 'state_exit' else ae_boundary(r),
             '未能归入明确过程类别，按内部辅助保留，待人工核验')

# dispatch by 构成要素
DISPATCH = {
    'iStage': cls_iStage,
    'bFirstRun': cls_FirstRun,
    'QZTJDJ_Code': cls_QZTJ,
    'ZHTJ_Code': cls_ZHTJ,
    'CATL_ZHTJ_FB::body': lambda r: F('内部辅助', 'AM强制停机点检弹窗复位（QZTJDJ_Code=5→0）',
        'QZTJDJ_Code处于5（弹窗复位阶段待命）且P_On',
        'QZTJDJ_Code写入0，弹窗复位完成',
        'CATL_ZHTJ_FB::body源码迁移T0000002（门控QZTJDJ_Code=5 AND P_On，guard_complete）；源码注释：AM强制停机点检弹窗复位',
        wired_boundary(r), '源码完整门控迁移；弹窗复位为内部辅助'),
    'DateTime.Hour': cls_DateTime, 'DateTime.Min': cls_DateTime, 'DateTime.Sec': cls_DateTime,
    'MachineAuotTime': cls_MachTime, 'MachineOpenTime': cls_MachTime, 'MachinePuttyTime': cls_MachTime,
    'MachineStopTime': cls_MachTime, 'MachineWaitTime': cls_MachTime,
    'MachineStatus': cls_MachStatus, 'FirstAlarmID': cls_FirstAlarm, 'UnitModeCurrent': cls_UnitMode,
    'iSys_ModeSet': cls_ModeSet,
    'iBcr_Cmd': cls_Bcr, 'iBcr_HcsStatus': cls_Bcr, 'iBcr_Rsp': cls_Bcr,
    'stScan.iBCRConNGTimesAlarmSet': cls_BcrAlarm,
    'stNgCount.today.arNgNumber[i]': cls_NGCount,
    'stShift.ishiftNum': cls_Shift, 'stShift.uiShifts': cls_Shift,
    'stShiftSelection.iCurrentDay': cls_Shift, 'stShiftSelection.iCurrentShift': cls_Shift,
    'InteractionStep': cls_PCComm, 'Out': cls_PCComm, 'Receive': cls_PCComm,
    'PLC_PC_Communication::body': cls_PCComm,
    'CATL_Login_Int': cls_Login, 'MES_Login_Int': cls_Login, 'stMesProc.ar_iProcDown[458]': cls_MesProc,
    'iProcDown': cls_Mes, 'iProcUp': cls_Mes, 'iProcResult': cls_Mes,
    'iMes_Rs': cls_OutStation, 'iMes_Rsp': cls_OutStation, 'stMes.iNGTimesAlarmSet': cls_NGAlarmSet,
    'stAD.rAnaHi': cls_AD, 'stAD.rAnaLw': cls_AD,
    'IN[i]': cls_SensorIn, 'IN[n]': cls_SensorIn, 'IN[p]': cls_SensorIn,
    'iAvoidOrCylinder': cls_Avoid,
    'IN_Done1': cls_CCD, 'IN_Done2': cls_CCD, 'IN_Ready': cls_CCD,
    'iMachineCode': cls_MachCode, 'iMode': cls_iMode,
    'iStep': cls_AntiStep, 'uiTimeOut': cls_AntiTimeout, 'iStationchoose': cls_StationChoose,
    'AutoVelocity': cls_Velocity, 'JogVelocity': cls_Velocity,
    'Modul': cls_ModulExit,
    'ClearBuffer': cls_TOM, 'MsgBools_Alarm_Mark[tempV+3]': cls_TOM,
    'StatEventsBufferOffset': cls_TOM, 'StatTriggerID': cls_TOM, 'tempWord.Array_B[2]': cls_TOM,
    'Msg_Bools.FirstOutAlarm': cls_Event, 'Msg_Category': cls_Event,
    'ReadDate_0': cls_GL20, '节点号': cls_GL20,
    'iLanguage': cls_Language,
    'rMagneticPathArc': cls_Magnetic, 'rMagneticPathLength': cls_Magnetic, 'rMagneticPathWidth': cls_Magnetic,
    'MovID[i]': cls_Mover, 'ar_iMoverCountArea[i]': cls_Mover,
    'ar_stCT[i2].iStep': cls_CT, 'iActNum': cls_CT, 'iActStep': cls_CT,
    'PR_Date.Sec': cls_PR,
    'MachineState': cls_MachineState,
    'temp5minCTU_CV': cls_5min,
    'i': cls_LoopVar, 'iPosId': cls_LoopVar,
    'stRmAxis.Par.iManualPosMode': cls_AxisPar, 'stRmAxis.Par.rSetTrqWarn': cls_AxisPar,
}
PREFIX_RULES = [
    ('HMI_ProStatistics.', cls_HMIStat),
    ('PackTags.Status.MachineStatus', cls_PackStat),
    ('PackTags.Status.UnitModeCurrent', cls_PackMode),
    ('stEM', cls_iCmdNo),
    ('stRmAxis.Par.', cls_AxisPar),
    ('ar_stRmAxisPos', cls_AxisPar),
    ('stRmAxisPdo.In.diFaultCode', cls_FaultCode),
    ('stRecipeCtrl.stRecipeCur.ar_rData', cls_Recipe),
    ('NOT (Clk) OR Clk_L', cls_Edge),
    ('NOT (Clk_L) AND Clk', cls_Edge),
    ('NOT (bProcessCmdRun) AND bCmdLast AND bStepCmdRun', cls_Edge),
    ('NOT (bStepCmdRun) AND bStopLast', cls_Edge),
]
def classify(r):
    key = r['构成要素']
    if key in DISPATCH:
        return DISPATCH[key](r)
    for pre, fn in PREFIX_RULES:
        if key.startswith(pre):
            return fn(r)
    return cls_default(r)

# C0000361/C0000362 are MC_Axis_FB coil writes -> mechanical action candidates
def classify_c361(r):
    if r['候选ID'] in ('C0000361', 'C0000362'):
        return cls_ModulCoil(r)
    return classify(r)

# ---------------- event reasons ----------------
def event_reason(e, controller):
    cmd = e['command']
    cmt = (e['comment'] or '').strip()[:60]
    base = None
    if cmd in ('coil_write', 'coil_set', 'coil_reset'):
        base = 'LD网络线圈动作观察（{cmd} {arg}），guard为空且无状态锚点，起止标签无法由候选核验'
    elif cmd == 'TON':
        base = 'TON定时器观察，无状态门控边界，起止标签无法由候选核验'
    elif cmd in ('CTU', 'Inc', 'Up'):
        base = '计数/边沿观察（{cmd}），无状态门控边界，起止标签无法由候选核验'
    elif cmd.startswith('\\\\Catl') or cmd in ('HMI_Confirm_FC', 'PrChectout_Station_full', 'PLC_PC_Communication',
                                              'CmdAndSC_Check_FC', 'EventSearch_FC', 'SummationInternalTransition_FC',
                                              'CATL_FB_StationPPM'):
        base = '功能块/函数调用观察（{cmd}），无状态门控边界，起止标签无法由候选核验'
    elif cmd.startswith('MC_'):
        base = '运动控制功能块调用观察（{cmd}），调用网络无状态门控，起止标签无法由候选核验'
    elif cmd in ('Get1sClk', 'Get1minClk', 'Get10msClk', 'GetTime', 'HeartBeat', 'Timer'):
        base = '系统时钟/心跳观察（{cmd}），非工艺过程，无状态边界'
    elif cmd in ('SktUDPCreate', 'SktUDPSend', 'SktUDPRcv', 'SktClose'):
        base = 'UDP套接字调用观察（{cmd}），其状态机所属ST体被解析器标记unsupported_ST，无状态边界候选'
    elif cmd == 'ST_assignment':
        base = 'ST赋值观察（{arg}），guard无状态锚点，起止标签无法由候选核验'
    elif cmd in ('Clear', 'ClearString', 'ArySearch', 'RIGHT', 'FOR', 'NEXT', 'LREAL_TO_REAL',
                 'LINT_TO_REAL', 'UINT_TO_REAL', 'DINT_TO_REAL', 'DIVTIME', 'MULTIME', 'ABS',
                 'TimeToSec', 'DtToDateStruct', 'DT_TO_DATE', 'AccumulationTimer'):
        base = '数据/运算指令观察（{cmd}），无状态门控边界'
    elif cmd.startswith('Reset') or cmd == 'GetEthercatFrameErrors':
        base = '诊断/复位调用观察（{cmd}），无状态门控边界'
    else:
        base = '动作观察（{cmd}），无状态门控边界，起止标签无法由候选核验'
    txt = base.format(cmd=cmd, arg=(e['arguments'] or '')[:40])
    if cmt:
        txt += '；源码注释：' + cmt
    return txt + '。需先建立带状态锚点的候选方可转为采集区间'

# ---------------- main ----------------
man = json.loads((RUN / 'workflow_manifest.json').read_text(encoding='utf-8'))
cmap = {}
with (RUN / 'indexed_candidates.csv').open(encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f):
        cmap[r['候选ID']] = r
emap = {}
with (RUN / 'indexed_events.csv').open(encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f):
        emap[r['事件ID']] = r

# global guard->candidate index for cross-batch anchored events (guards without
# "controller=" pattern were packed into __ungrouped_events__ by the workflow)
start_index = {}
for cid, r in cmap.items():
    start_index.setdefault(norm(r['开始标签']), []).append(cid)

cross_ref = {}   # candidate_id -> list of event ids anchored to it from other batches
for eid, ev in emap.items():
    g = norm(ev['guard'])
    if not g:
        continue
    for cid in start_index.get(g, []):
        ns = norm(ev['next_state'])
        if ns and norm(cmap[cid]['结束标签']) and ns != norm(cmap[cid]['结束标签']):
            continue
        cross_ref.setdefault(cid, []).append(eid)

steps_total = 0
unresolved_total = 0
for b in man['batches']:
    d = json.loads((RUN / b['input']).read_text(encoding='utf-8'))
    steps = []
    unresolved = []
    used_events = set()
    batch_event_ids = {ev['事件ID'] for ev in d['events']}
    # candidate steps
    for c in d['candidates']:
        f6 = classify_c361(c)
        # attach events whose guard matches candidate start (and next_state matches end when present)
        attach = []
        for ev in d['events']:
            g = norm(ev['guard'])
            if not g:
                continue
            if g == norm(c['开始标签']):
                ns = norm(ev['next_state'])
                if ns and norm(c['结束标签']) and ns != norm(c['结束标签']):
                    continue
                attach.append(ev['事件ID'])
                used_events.add(ev['事件ID'])
        # cross-batch anchored events are documented in the step note (cannot be
        # referenced across batches; they stay in their own batch's unresolved list)
        note = f6['说明']
        xrefs = [eid for eid in cross_ref.get(c['候选ID'], [])
                 if eid not in used_events and eid not in batch_event_ids]
        if xrefs:
            note = note + '；跨批锚定事件' + ';'.join(xrefs) + '（guard与本候选开始标签一致，见其所在批次unresolved_events记录）'
        steps.append({
            '构成要素': c['构成要素'], '动作': c['动作'], '动作描述': c['动作描述'],
            '开始标签': c['开始标签'], '结束标签': c['结束标签'],
            '核验状态': 'confirmed' if c['状态'] == 'native_wired_transition' else 'candidate',
            '证据候选ID': [c['候选ID']],
            '证据事件ID': attach,
            '过程类型': f6['过程类型'], '统计对象': f6['统计对象'],
            '起点含义': f6['起点含义'], '终点含义': f6['终点含义'],
            '工艺依据': f6['工艺依据'], '边界依据': f6['边界依据'],
            '说明': note,
        })
    # remaining events -> unresolved
    for ev in d['events']:
        if ev['事件ID'] not in used_events:
            reason = event_reason(ev, b['controller'])
            g = norm(ev['guard'])
            if g and g in start_index:
                reason = reason + '；该事件guard与本批外候选开始标签一致：候选' + \
                         ','.join(start_index[g]) + '（已在该候选的step说明中互引）'
            unresolved.append({'event_id': ev['事件ID'], 'reason': reason})
    result = {
        'batch_id': d['batch_id'],
        'steps': steps,
        'unresolved_events': unresolved,
        'notes': '分类基于源码POU/注释/状态观察；全部候选边界规范化状态为待核验或动作观察，'
                 '无全局退出等价证明；主候选池未删减；事件按无状态锚点如实进入unresolved_events。'
                 if steps else '本批无候选，仅有无状态锚点的动作观察，全部按协议进入unresolved_events。',
    }
    (RUN / b['output']).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    steps_total += len(steps)
    unresolved_total += len(unresolved)

print('batches:', len(man['batches']), '| steps:', steps_total, '| unresolved:', unresolved_total)
