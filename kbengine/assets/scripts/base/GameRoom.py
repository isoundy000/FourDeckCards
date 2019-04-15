# -*- coding: utf-8 -*-
import math

import KBEngine
from KBEDebug import *
import time
from datetime import datetime
from interfaces.GameObject import GameObject
from entitymembers.iRoomRules import iRoomRules
from entitymembers.PlayerProxy import PlayerProxy
from BaseEntity import BaseEntity
import json
import const
import switch
import utility
import copy
from Functor import Functor


class GameRoom(BaseEntity, GameObject, iRoomRules):
	"""
	这是一个游戏房间/桌子类
	该类处理维护一个房间中的实际游戏， 例如：斗地主、麻将等
	该房间中记录了房间里所有玩家的mailbox，通过mailbox我们可以将信息推送到他们的客户端。
	"""
	def __init__(self):
		BaseEntity.__init__(self)
		GameObject.__init__(self)
		iRoomRules.__init__(self)

		self.agent = None
		self.roomID = utility.gen_room_id()

		# 状态0：未开始游戏， 1：某一局游戏中
		self.state = const.ROOM_WAITING

		# 存放该房间内的玩家mailbox
		self.players_dict = {}
		self.players_list = [None] * self.player_num
		self.origin_players_list = [None] * self.player_num

		# 打出财神的index
		self.discard_king_idx = -1

		# 庄家index
		self.dealer_idx = 0
		# 当前控牌的玩家index
		self.current_idx = 0
		# 对当前打出的牌可以进行操作的玩家的index, 服务端会限时等待他的操作
		# 房间基础轮询timer
		self._poll_timer = None
		# 玩家操作限时timer
		self._op_timer = None
		# 一局游戏结束后, 玩家准备界面等待玩家确认timer
		self._next_game_timer = None

		#财神(多个)
		self.kingTiles = []
		#圈风
		self.prevailing_wind = const.WIND_EAST
		#一圈中玩家坐庄次数
		self.dealerNumList = [0] * self.player_num

		self.current_round = 0
		self.all_discard_tiles = []
		# 最后一位出牌的玩家
		self.last_player_idx = -1
		# 房间开局所有操作的记录(aid, src, des, tile)
		self.op_record = []
		# 房间开局操作的记录对应的记录id
		self.record_id = -1
		# 确认继续的玩家
		self.confirm_next_idx = []
		# 解散房间操作的发起者
		self.dismiss_room_from = -1
		# 解散房间操作开始的时间戳
		self.dismiss_room_ts = 0
		# 解散房间操作投票状态
		self.dismiss_room_state_list = [0] * self.player_num
		self.dismiss_timer = None
		# 房间创建时间
		self.roomOpenTime = time.time()
		# 玩家操作列表
		self.wait_op_info_list = []
		# 杠后摸牌延时操作时的标志位，例如主要在延时中出现解散房间操作时需要拒绝操作，同上
		self.wait_force_delay_kong_draw = False

		# 牌局记录
		self.game_result = {}
		# 当前老庄数
		self.cur_dealer_mul = self.begin_dealer_mul

		# 房间所属的茶楼桌子, 仅茶楼中存在
		self.club_table = None
		# 增加房间销毁定时器
		self.timeout_timer = self.add_timer(const.ROOM_TTL, self.timeoutDestroy)

		# 本轮出牌分数
		self.curround_score = 0
		# 一轮刚开始的标志
		self.round_start = True
		# 玩家要不起的标志位
		self.op_pass_flag = 0
		# 玩家胜利个数，每当有玩家胜利，个数加一
		self.player_win_num = 0
		# 控牌玩家出牌列表
		self.controller_discard_list = []
		# 等待出牌的玩家
		self.waitIdx = -1
		# 玩家出牌列表
		self.deskPokerList = [[],[],[],[]]
		# 胜利玩家列表
		self.win_list = []
		# 玩家本局游戏彻底结束的列表
		self.round_end_list = []
		# 玩家本局得分（除了奖励分）
		self.curScoreList = [0] * self.player_num
		# 玩家本局最终得分（除了奖励分）
		self.lastScoreList = [0] * self.player_num
		# 玩家本局得奖
		self.curPrizeList = [0] * self.player_num

	def _reset(self):
		self.state = const.ROOM_WAITING
		self.agent = None
		self.players_list = [None] * self.player_num
		self.discard_king_idx = -1
		self.dealer_idx = 0
		self.current_idx = 0
		self.player_win_num = 0
		self.waitIdx = 0
		self.op_pass_flag = 0
		self.controller_discard_list = []
		self.deskPokerList = [[],[],[],[]]
		self.curround_score = 0
		self.round_start = True
		self.round_end_list = []
		self.win_list = []
		self._poll_timer = None
		self._op_timer = None
		self._next_game_timer = None
		self.all_discard_tiles = []
		self.kingTiles = []
		self.current_round = 0
		self.confirm_next_idx = []
		self.prevailing_wind = const.WIND_EAST
		self.dismiss_timer = None
		self.dismiss_room_ts = 0
		self.dismiss_room_state_list = [0, 0, 0, 0]
		self.wait_op_info_list = []
		self.cur_dealer_mul = self.begin_dealer_mul
		KBEngine.globalData["GameWorld"].delRoom(self)
		# 茶楼座位信息变更
		if self.club_table:
			self.club_table.seatInfoChanged()
			self.club_table.room = None
		self.destroySelf()

	@property
	def prefixLogStr(self):
		""" only on Log """
		return 'room:{},curround:{}'.format(self.roomID, self.current_round)

	@property
	def isFull(self):
		count = sum([1 for i in self.players_list if i is not None])
		return count == self.player_num

	@property
	def isEmpty(self):
		count = sum([1 for i in self.players_list if i is not None])
		return count == 0 and self.room_type != const.AGENT_ROOM

	@property
	def nextIdx(self):
		# tryNext = (self.current_idx + 1) % self.player_num
		# for j in range(2):
		# 	for i in range(self.player_num):
		# 		if self.player_num > tryNext:
		# 			return tryNext
		# 		tryNext = (tryNext + 1) % self.player_num
		return (self.current_idx + 1) % self.player_num

	@property
	def wreathsList(self):
		return [p.wreaths for i,p in enumerate(self.players_list)]

	@property
	def windsList(self):
		return [p.wind for i,p in enumerate(self.players_list)]

	@property
	def club(self):
		try:
			if self.club_table:
				return self.club_table.club
		except:
			# 引用代理的对象可能已经被destroy, 比如解散茶楼时
			pass
		return None

	def getSit(self):
		for i, j in enumerate(self.players_list):
			if j is None:
				return i
		return None

	def sendEmotion(self, avt_mb, eid):
		""" 发表情 """
		# DEBUG_MSG("Room.Player[%s] sendEmotion: %s" % (self.roomID, eid))
		idx = None
		for i, p in enumerate(self.players_list):
			if p and avt_mb == p.mb:
				idx = i
				break
		if idx is None:
			return

		for i, p in enumerate(self.players_list):
			if p and i != idx:
				p.mb.recvEmotion(idx, eid)

	def sendMsg(self, avt_mb, mid, msg):
		""" 发消息 """
		# DEBUG_MSG("Room.Player[%s] sendMsg: %s" % (self.roomID, mid))
		idx = None
		for i, p in enumerate(self.players_list):
			if p and avt_mb == p.mb:
				idx = i
				break
		if idx is None:
			return

		for i, p in enumerate(self.players_list):
			if p and i != idx:
				p.mb.recvMsg(idx, mid, msg)

	def sendExpression(self, avt_mb, fromIdx, toIdx, eid):
		""" 发魔法表情 """
		# DEBUG_MSG("Room.Player[%s] sendEmotion: %s" % (self.roomID, eid))
		idx = None
		for i, p in enumerate(self.players_list):
			if p and avt_mb == p.mb:
				idx = i
				break
		if idx is None:
			return

		for i, p in enumerate(self.players_list):
			if p and i != idx:
				p.mb.recvExpression(fromIdx, toIdx, eid)

	def sendVoice(self, avt_mb, url):
		# DEBUG_MSG("Room.Player[%s] sendVoice" % (self.roomID))
		idx = None
		for i, p in enumerate(self.players_list):
			if p and avt_mb.userId == p.userId:
				idx = i
				break
		if idx is None:
			return

		for i, p in enumerate(self.players_list):
			if p and p.mb:
				p.mb.recvVoice(idx, url)

	def sendAppVoice(self, avt_mb, url, time):
		# DEBUG_MSG("Room.Player[%s] sendVoice" % (self.roomID))
		idx = None
		for i, p in enumerate(self.players_list):
			if p and avt_mb.userId == p.userId:
				idx = i
				break
		if idx is None:
			return

		for i, p in enumerate(self.players_list):
			if p and p.mb and i != idx:
				p.mb.recvAppVoice(idx, url, time)


	def apply_dismiss_room(self, avt_mb):
		""" 游戏开始后玩家申请解散房间 """
		if self.dismiss_timer is not None:
			self.vote_dismiss_room(avt_mb, 1)
			return
		self.dismiss_room_ts = time.time()
		src = None
		for i, p in enumerate(self.players_list):
			if p.userId == avt_mb.userId:
				src = p
				break

		# 申请解散房间的人默认同意
		self.dismiss_room_from = src.idx
		self.dismiss_room_state_list[src.idx] = 1

		def dismiss_callback():
			self.saveRoomResult()
			self.give_up_record_game()
			# self.dropRoom()
			self.do_drop_room()

		self.dismiss_timer = self.add_timer(const.DISMISS_ROOM_WAIT_TIME, dismiss_callback)

		for p in self.players_list:
			if p and p.mb and p.userId != avt_mb.userId:
				p.mb.req_dismiss_room(src.idx)

	def vote_dismiss_room(self, avt_mb, vote):
		""" 某位玩家对申请解散房间的投票 """
		if self.wait_force_delay_kong_draw:
			return
		src = None
		for p in self.players_list:
			if p and p.userId == avt_mb.userId:
				src = p
				break

		self.dismiss_room_state_list[src.idx] = vote
		for p in self.players_list:
			if p and p.mb:
				p.mb.vote_dismiss_result(src.idx, vote)

		yes = self.dismiss_room_state_list.count(1)
		no = self.dismiss_room_state_list.count(2)
		if yes >= 3:
			if self.dismiss_timer:
				self.cancel_timer(self.dismiss_timer)
				self.dismiss_timer = None
			self.dismiss_timer = None

			self.saveRoomResult()
			self.give_up_record_game()
			# self.dropRoom()
			self.do_drop_room()


		if no >= 2:
			if self.dismiss_timer:
				self.cancel_timer(self.dismiss_timer)
				self.dismiss_timer = None
			self.dismiss_timer = None
			self.dismiss_room_from = -1
			self.dismiss_room_ts = 0
			self.dismiss_room_state_list = [0,0,0,0]

	def notify_player_online_status(self, userId, status):
		src = -1
		for idx, p in enumerate(self.players_list):
			if p and p.userId == userId:
				p.online = status
				src = idx
				break

		if src == -1:
			return

		for idx, p in enumerate(self.players_list):
			if p and p.mb and p.userId != userId:
				p.mb.notifyPlayerOnlineStatus(src, status)

	def reqEnterRoom(self, avt_mb, first=False):
		"""
		defined.
		客户端调用该接口请求进入房间/桌子
		"""
		if self.isFull:
			avt_mb.enterRoomFailed(const.ENTER_FAILED_ROOM_FULL)
			return
		if self.room_type == const.CLUB_ROOM:
			if self.club and not self.club.isMember(avt_mb.userId):
				avt_mb.enterRoomFailed(const.ENTER_FAILED_NOT_CLUB_MEMBER)
				return

		def _check_user_info(content):
			if content is None:
				DEBUG_MSG("room:{0},curround:{1} userId:{2} enterRoomFailed callback error: content is None".format(self.roomID, self.current_round, avt_mb.userId))
				if not first:
					avt_mb.enterRoomFailed(const.CREATE_FAILED_NET_SERVER_ERROR)
				return False
			try:
				data = json.loads(content)
				card_cost, diamond_cost = switch.calc_cost(self.game_round, self.getCalCostNeed())
				if card_cost > data["card"]:
					avt_mb.enterRoomFailed(const.ENTER_FAILED_ROOM_DIAMOND_NOT_ENOUGH)
					return False
			except:
				err, msg, stack = sys.exc_info()
				DEBUG_MSG("room:{0},curround:{1} _check_user_info callback error:{2} , exc_info: {3} ,{4}".format(self.roomID, self.current_round, content, err, msg))
				avt_mb.enterRoomFailed(const.CREATE_FAILED_OTHER)
				return False
			return True

		def callback():
			if self.isDestroyed:
				avt_mb.enterRoomFailed(const.ENTER_FAILED_ROOM_DESTROYED)
				return
			for i, p in enumerate(self.players_list):
				if p and p.mb and p.mb.userId == avt_mb.userId:
					p.mb = avt_mb
					avt_mb.enterRoomSucceed(self, i)
					return

			DEBUG_MSG("{} userId:{} reqEnterRoom".format(self.prefixLogStr, avt_mb.userId))
			idx = self.getSit()
			# AA支付的情况下, 可能多个玩家同时走到这里
			if idx is None:
				avt_mb.enterRoomFailed(const.ENTER_FAILED_ROOM_FULL)
				return
			n_player = PlayerProxy(avt_mb, self, idx)
			self.players_dict[avt_mb.userId] = n_player
			self.players_list[idx] = n_player

			# 茶楼座位信息变更
			if self.club_table:
				self.club_table.seatInfoChanged()

			# 确认准备,不需要手动准备
			if self.hand_prepare == const.AUTO_PREPARE:
				self.prepare(avt_mb)

			if not first:
				self.broadcastEnterRoom(idx)
			else:
				avt_mb.createRoomSucceed(self)
			self.ready_after_prepare()
		if switch.DEBUG_BASE:
			callback()
		else:
			if first or self.pay_mode != const.AA_PAY_MODE:
				callback()
			else:
				def _user_info_callback(content):
					if _check_user_info(content):
						callback()

				utility.get_user_info(avt_mb.accountName, _user_info_callback)

	def client_prepare(self, avt_mb):
		DEBUG_MSG("room:{0},curround:{1} client_prepare userId:{2}".format(self.roomID, self.current_round, avt_mb.userId))
		self.prepare(avt_mb)
		self.ready_after_prepare()

	def prepare(self, avt_mb):
		""" 第一局/一局结束后 玩家准备 """
		if self.state == const.ROOM_PLAYING or self.state == const.ROOM_TRANSITION:
			return

		idx = -1
		for i, p in enumerate(self.players_list):
			if p and p.userId == avt_mb.userId:
				idx = i
				break

		if idx not in self.confirm_next_idx:
			self.confirm_next_idx.append(idx)
			for p in self.players_list:
				if p and p.idx != idx:
					p.mb.readyForNextRound(idx)

	def ready_after_prepare(self):
		if len(self.confirm_next_idx) == self.player_num and self.isFull and self.state == const.ROOM_WAITING:
			self.pay2StartGame()

	def reqReconnect(self, avt_mb):
		DEBUG_MSG("room:{0},curround:{1} avt_mb reqReconnect userid:{2}".format(self.roomID, self.current_round, avt_mb.userId))
		if avt_mb.userId not in self.players_dict.keys():
			return

		DEBUG_MSG("room:{0},curround:{1} avt_mb reqReconnect player:{2} is in room".format(self.roomID, self.current_round, avt_mb.userId))
		# 如果进来房间后牌局已经开始, 就要传所有信息
		# 如果还没开始, 跟加入房间没有区别
		player = self.players_dict[avt_mb.userId]
		player.mb = avt_mb
		player.online = 1
		if self.state == const.ROOM_PLAYING or 0 < self.current_round <= self.game_round:
			if self.state == const.ROOM_WAITING:
				# 重连回来直接准备
				self.client_prepare(avt_mb)
			rec_room_info = self.get_reconnect_room_dict(player.mb.userId)
			player.mb.handle_reconnect(rec_room_info)
			if len(self.getTipsCards()) == 0 and self.players_list[self.current_idx].online and not self.round_start:
				self.players_list[self.current_idx].mb.doOperation(const.OP_PASS, [])
		else:
			sit = 0
			for idx, p in enumerate(self.players_list):
				if p and p.mb:
					if p.mb.userId == avt_mb.userId:
						sit = idx
						break
			avt_mb.enterRoomSucceed(self, sit)


	def reqLeaveRoom(self, player):
		"""
		defined.
		客户端调用该接口请求离开房间/桌子
		"""
		DEBUG_MSG("room:{0},curround:{1} reqLeaveRoom userId:{2}, room_type:{3}, state:{4}".format(self.roomID, self.current_round, player.userId, self.room_type, self.state))
		if self.state != const.ROOM_WAITING:
			DEBUG_MSG("{} reqLeaveRoom: not allow ".format(self.prefixLogStr))
			# player.quitRoomFailed(-1)
			return
		if player.userId in self.players_dict.keys():
			n_player = self.players_dict[player.userId]
			idx = n_player.idx

			if idx == 0 and self.room_type == const.NORMAL_ROOM:
				# 房主离开房间, 则解散房间
				self.give_up_record_game()
				# self.dropRoom()
				self.do_drop_room()
			else:
				n_player.mb.quitRoomSucceed()
				self.players_list[idx] = None
				del self.players_dict[player.userId]
				if idx in self.confirm_next_idx:
					self.confirm_next_idx.remove(idx)
				# 通知其它玩家该玩家退出房间
				for i, p in enumerate(self.players_list):
					if i != idx and p and p.mb:
						p.mb.othersQuitRoom(idx)

		# 茶楼座位信息变更
		if self.room_type == const.CLUB_ROOM and self.club_table:
			self.club_table.seatInfoChanged()

		if self.isEmpty:
			self.give_up_record_game()
			# self.dropRoom()
			self.do_drop_room()

	def dropRoom(self):
		self.dismiss_timer = None
		for i,p in enumerate(self.players_list):
			if p and p.mb:
				try:
					p.mb.quitRoomSucceed()
				except:
					pass

		if self.room_type == const.AGENT_ROOM and self.agent:
			# 将房间从代理房间中删除
			if not self.agent.isDestroyed:
				self.agent.agentRoomDropped(self.roomID)

			try:
				# 如果是代开房, 没打完一局返还房卡
				if switch.DEBUG_BASE == 0 and self.current_round < 1 and self.pay_mode == const.AGENT_PAY_MODE:
					card_cost, diamond_cost = switch.calc_cost(self.game_round, self.getCalCostNeed())

					def callback(room_id, user_id, content):
						try:
							content = content.decode()
							if content[0] != '{':
								DEBUG_MSG(content)
								return
						except:
							DEBUG_MSG("dropRoom{} AgentRoom return Failed, userID = {}. return {} back".format(room_id, user_id, (card_cost, diamond_cost)))

					utility.update_card_diamond(self.agent.accountName, card_cost, diamond_cost,
												Functor(callback, self.roomID, self.agent.userId), "FourDeckCards drop AgentRoomID:{}".format(self.roomID))  # reason 必须为英文
			except:
				pass

		self._reset()

	def do_drop_room(self):
		if self.game_result:
			if len(self.game_result['round_result']) == 0:
				self.dropRoom()
			else:
				self.subtotal_result()
		else:
			self.dropRoom()

	def broadcastOperation2(self, idx, aid, tile_list = None, curround_score = 0):
		""" 将操作广播除了自己之外的其他人 """
		for i, p in enumerate(self.players_list):
			if p and i != idx:
				p.mb.postOperation(idx, aid, tile_list, curround_score)

	def broadcastMultiOperation(self, idx_list, aid_list, tile_list=None):
		for i, p in enumerate(self.players_list):
			if p is not None:
				p.mb.postMultiOperation(idx_list, aid_list, tile_list)

	def broadcastRoundEnd(self, info):
		# 广播胡牌或者流局导致的每轮结束信息, 包括算的扎码和当前轮的统计数据

		# 先记录玩家当局战绩, 会累计总得分
		self.record_round_result()

		self.state = const.ROOM_WAITING
		DEBUG_MSG("room:{0},curround:{1} broadcastRoundEnd state:{2}".format(self.roomID, self.current_round, self.state))
		# info['left_tiles'] = self.tiles
		info['player_info_list'] = [p.get_round_client_dict() for p in self.players_list if p is not None]

		DEBUG_MSG("room:{0},curround:{1}=={2}".format(self.roomID, self.current_round, "&" * 30))
		DEBUG_MSG("room:{0},curround:{1} RoundEnd info:{2}".format(self.roomID, self.current_round, info))

		self.confirm_next_idx = []
		for p in self.players_list:
			if p:
				p.mb.roundResult(info)

		# self.end_record_game(info)

	def pay2StartGame(self):
		""" 开始游戏 """
		DEBUG_MSG("room:{},curround:{},game_mode:{},base_score:{},king_mode:{},begin_dealer_mul:{},win_mode:{},three_job:{},pong_useful:{},bao_tou:{},round_max_lose:{},game_max_lose:{},game_round:{},hand_prepare:{} pay2StartGame state:{}".format(self.roomID, self.current_round,
		self.game_mode, self.base_score, self.king_mode, self.begin_dealer_mul, self.win_mode, self.three_job,
		self.pong_useful, self.bao_tou, self.round_max_lose, self.game_max_lose, self.game_round, self.hand_prepare,
		self.state))

		if self.timeout_timer:
			self.cancel_timer(self.timeout_timer)
			self.timeout_timer = None

		self.state = const.ROOM_TRANSITION

		if self.current_round == 0:
			# 第一局备份原始座位
			self.origin_players_list = self.players_list[:]
			self.dealer_idx = 0

		# 仅仅在第1局扣房卡, 不然每局都会扣房卡
		if self.current_round == 0:
			if switch.DEBUG_BASE:
				self.paySuccessCbk()
				return

			card_cost, diamond_cost = switch.calc_cost(self.game_round, self.getCalCostNeed())
			if self.pay_mode == const.NORMAL_PAY_MODE:
				pay_account = self.origin_players_list[0].mb.accountName
				reason = "FourDeckCards RoomID:{}".format(self.roomID)

				def pay_callback(content):
					if self._check_pay_callback(content):
						self.paySuccessCbk()

				utility.update_card_diamond(pay_account, -card_cost, -diamond_cost, pay_callback, reason)
			elif self.pay_mode == const.CLUB_PAY_MODE:
				pay_account = self.club.owner['accountName']
				reason = "FourDeckCards Club:{} RoomID:{}".format(self.club.clubId, self.roomID)

				def pay_callback(content):
					if self._check_pay_callback(content):
						self.paySuccessCbk()

				utility.update_card_diamond(pay_account, -card_cost, -diamond_cost, pay_callback, reason)
			elif self.pay_mode == const.AGENT_PAY_MODE:
				# 开房的时候已经扣了房卡
				self.paySuccessCbk()
			elif self.pay_mode == const.AA_PAY_MODE:
				pay_accounts = [p.mb.accountName for p in self.players_list]
				if self.club:
					reason = "FourDeckCards Club:{} AA RoomID:{}".format(self.club.clubId, self.roomID)
				else:
					reason = "FourDeckCards AA RoomID:{}".format(self.roomID)

				def pay_callback(content):
					if self._check_aa_pay_callback(content):
						self.paySuccessCbk()

				utility.update_card_diamond_aa(pay_accounts, -card_cost, -diamond_cost, pay_callback, reason)
			else:
				ERROR_MSG("pay2StartGame Error: No this PayMode:{}".format(self.pay_mode))
				return
		else:
			self.paySuccessCbk()

	def _check_pay_callback(self, content):
		if content is None or content[0] != '{':
			DEBUG_MSG('{} pay callback {}'.format(self.prefixLogStr, content))
			self.give_up_record_game()
			# self.dropRoom()
			self.do_drop_room()
			return False
		return True

	def _check_aa_pay_callback(self, content):
		res = True
		try:
			ret = json.loads(content)
			if ret['errcode'] != 0:
				res = False
				DEBUG_MSG('room:{},cur_round:{} aa pay callback error code={}, msg={}'.format(self.roomID, self.current_round, ret['errcode'], ret['errmsg']))
		except:
			res = False
			import traceback
			ERROR_MSG(traceback.format_exc())

		if not res:
			self.give_up_record_game()
			self.do_drop_room()
			return False
		return True

	# 扣房卡/钻石成功后开始游戏(不改动部分)
	def paySuccessCbk(self):
		DEBUG_MSG("room:{},curround:{} paySuccessCbk state:{}".format(self.roomID, self.current_round, self.state))
		try:
			# 第一局时房间默认房主庄家, 之后谁上盘赢了谁是, 如果臭庄, 上一把玩家继续坐庄
			swap_list = [0,1,2,3]
			# 	self.swapSeat(swap_list)

			self.op_record = []
			# self.op_special_record = []
			self.state = const.ROOM_PLAYING
			self.current_round += 1
			self.all_discard_tiles = []

			for p in self.players_list:
				p.reset()

			self.current_idx = self.dealer_idx
			self.discard_king_idx = -1

			def begin(prefabHandTiles=None):
				# self.setPrevailingWind()  					# 圈风
				# self.setPlayerWind()  						# 位风
				self.initTiles()  							# 牌堆
				self.deal(prefabHandTiles)  	# 发牌
				# self.kongWreath()  							# 杠花
				# self.addWreath()  							# 补花
				# self.rollKingTile(prefabKingTiles)  		# 财神
				beginTilesList = [copy.deepcopy(p.tiles) for i, p in enumerate(self.players_list)]
				self.tidy()  								# 整理
				self.beginRound(True)  						# 第一张牌优先抓，后开始游戏
				# beginTilesList[self.current_idx].append(self.players_list[self.current_idx].last_draw)
				self.startGame(beginTilesList, swap_list)

			if switch.DEBUG_BASE == 0:
				begin([], [[] for i in range(self.player_num)], [])
			elif switch.DEBUG_BASE == 1: # 开发模式 除去不必要的通信时间 更接近 真实环境
				prefabHandTiles = [
					[],
					[],
					[],
					[]
				]
				begin(prefabHandTiles)
			else:
				def callback(content):
					DEBUG_MSG("room:{},curround:{} debugmode,content:{}".format(self.roomID, self.current_round,content))
					if content is None or content == "10000" or content[0:2] != "ok": # 10000代表找不到该文件
						begin()
					else:
						try:
							content = content[2:]
							data = json.loads(content)
							DEBUG_MSG("room:{},curround:{} data:{}".format(self.roomID, self.current_round, data))
							handTiles = [[] for i in range(self.player_num)]
							# 检查数据
							for k,v in enumerate(data["handTiles"]):
								if k < self.player_num:
									for t in v:
										if utility.validTile(t):
											handTiles[k].append(t)
							begin(handTiles)
						except:
							err, msg, stack = sys.exc_info()
							DEBUG_MSG("room:{},curround:{} try begin error; exc_info: {} ,{}".format(self.roomID, self.current_round, err, msg))

				utility.getDebugPrefab(self.origin_players_list[0].mb.accountName, callback)
		except:
			err, msg, stack = sys.exc_info()
			DEBUG_MSG("room:{},curround:{} paySuccessCbk error; exc_info: {} ,{}".format(self.roomID, self.current_round, err, msg))
			DEBUG_MSG("room:{},curround:{} consume failed! users: {}".format(self.roomID, self.current_round, [p.userId for p in self.origin_players_list if p]))

	# 玩家开始游戏
	def startGame(self, beginTilesList, swap_list):
		self.wait_force_delay_kong_draw = False
		DEBUG_MSG("room:{},curround:{} start game swap_list:{}".format(self.roomID, self.current_round, swap_list))
		diceList = self.throwDice([self.dealer_idx])
		DEBUG_MSG("room:{},curround:{} start game info:{}".format(self.roomID, self.current_round, self.dealer_idx))
		for i,p in enumerate(self.players_list):
			if p and p.mb:
				DEBUG_MSG("room:{},curround:{} start tiles:{}".format(self.roomID, self.current_round, p.tiles))
		for i,p in enumerate(self.players_list):
			if p and p.mb:
				DEBUG_MSG("room:{},curround:{} start idx:{} begin tiles:{}".format(self.roomID, self.current_round, i, beginTilesList[i]))
				p.mb.startGame(self.dealer_idx, beginTilesList[i], swap_list)
		self.begin_record_game(diceList)

	def cutAfterKong(self):
		if len(self.tiles) <= self.lucky_num + const.END_TILE_NUMBER:
			self.drawEnd()
		elif len(self.tiles) > self.lucky_num + const.END_TILE_NUMBER + 1:
			player = self.players_list[self.current_idx]
			ti = self.tiles[0]
			self.tiles = self.tiles[1:]
			player.cutTile(ti)

	def beginRound(self, is_first = False):
		pass
		# if len(self.tiles) <= self.lucky_num + const.END_TILE_NUMBER:
		# 	self.drawEnd()
		# 	return
		# ti = self.tiles[0]
		# self.tiles = self.tiles[1:]
		# DEBUG_MSG("room:{0},curround:{1} idx:{2} beginRound tile:{3} leftNum:{4}".format(self.roomID, self.current_round, self.current_idx, ti, len(self.tiles)))
		# p = self.players_list[self.current_idx]
		# p.drawTile(ti, is_first)

	def drawEnd(self):
		DEBUG_MSG("room:{0},curround:{1} drawEnd.".format(self.roomID, self.current_round))
		""" 臭庄 """
		lucky_tiles = ""
		# self.cal_lucky_tile_score(lucky_tiles, -1)
		self.settlement()
		info = dict()
		info['win_op'] = -1
		info['win_idx'] = -1
		info['lucky_tiles'] = lucky_tiles
		info['result_list'] = []
		info['finalTile'] = 0
		info['from_idx'] = -1
		info['multiply'] = 0
		info['dealer_idx'] = self.dealer_idx
		info['cur_dealer_mul'] = self.cur_dealer_mul
		info['job_relation'] = []
		DEBUG_MSG("room:{0},curround:{1} drawEnd INFO:{2}".format(self.roomID, self.current_round, info))
		if self.current_round < self.game_round: # 在打片模式下 流局必然 继续
			self.broadcastRoundEnd(info)
		else:
			self.endAll(info)

	def winGame(self, idx, op, finalTile, from_idx, score, result):
		""" 座位号为idx的玩家胡牌 """ 
		# self.deskPokerList[idx] = []
		self.win_list.append(idx)
		if len(self.win_list) == self.player_num - 1:
			self.lastScoreList = copy.deepcopy(self.curScoreList)
			surScore = 0
			for i,score in enumerate(self.lastScoreList):
				if score % 10 == 5:
					self.lastScoreList[i] -= 5
					surScore += 5
				self.lastScoreList[i] -= 100
			self.lastScoreList[self.win_list[0]] += surScore
		self.cal_score(idx, self.win_list, op)
		self.broadcastOperation(idx, op, [], self.curround_score, self.curScoreList)
		DEBUG_MSG("room:{0},curround:{1} score0:{2} score1:{3} score2:{4} score3:{5}".format(self.roomID, self.current_round, self.players_list[0].score, self.players_list[1].score, self.players_list[2].score, self.players_list[3].score))
		if len(self.win_list) != self.player_num - 1:
			return

		self.settlement()
		prizeScoreList = [0] * self.player_num
		for i in range(len(self.curPrizeList)):
			prize_score = 0
			for j in range(len(self.curPrizeList)):
				if i == j:
					prize_score += self.curPrizeList[j] * 90
				else:
					prize_score -= self.curPrizeList[j] * 30
			prizeScoreList[i] = prize_score
		info = dict()
		DEBUG_MSG("room:{0},curround:{1} roundEnd win_list:{2} curScoreList:{3} curPrizeList:{4}".format(self.roomID, self.current_round, self.win_list, self.curScoreList, self.curPrizeList))
		info['win_list'] = self.win_list
		info['curScoreList'] = self.curScoreList
		info['lastScoreList'] = self.lastScoreList
		info['prizeScoreList'] = prizeScoreList
		info['dealer_idx'] = last_dealer_idx = self.dealer_idx
		self.dealer_idx = self.win_list[0]
		self.round_start = True
		self.current_idx = self.win_list[0]
		self.waitIdx = self.win_list[0]
		self.op_pass_flag = 0
		self.controller_discard_list = []
		self.deskPokerList = [[],[],[],[]]
		self.curround_score = 0
		self.round_end_list = []
		self.win_list = []
		self.curScoreList = [0] * self.player_num
		self.lastScoreList = [0] * self.player_num
		self.curPrizeList = [0] * self.player_num
		if self.current_round < self.game_round:
			self.broadcastRoundEnd(info)
		else:
			self.endAll(info)

	def begin_record_game(self, diceList):
		DEBUG_MSG("room:{0},curround:{1} begin record game".format(self.roomID, self.current_round))
		self.begin_record_room()
		KBEngine.globalData['GameWorld'].begin_record_room(self, self.roomID, self, diceList)

	def begin_record_callback(self, record_id):
		self.record_id = record_id

	def end_record_game(self, result_info):
		DEBUG_MSG("room:{0},curround:{1} end record game".format(self.roomID, self.current_round))
		KBEngine.globalData['GameWorld'].end_record_room(self.roomID, self, result_info)
		self.record_id = -1

	def give_up_record_game(self):
		DEBUG_MSG("room:{0},curround:{1} give up record game".format(self.roomID, self.current_round))
		KBEngine.globalData['GameWorld'].give_up_record_room(self.roomID)

	def settlement(self):
		for i,p in enumerate(self.players_list):
			if p is not None:
				p.settlement()

	def endAll(self, info):
		""" 游戏局数结束, 给所有玩家显示最终分数记录 """

		# 先记录玩家当局战绩, 会累计总得分
		self.record_round_result()

		# info['left_tiles'] = self.tiles
		info['player_info_list'] = [p.get_round_client_dict() for p in self.players_list if p is not None]
		player_info_list = [p.get_final_client_dict() for p in self.players_list if p is not None]
		DEBUG_MSG("room:{0},curround:{1} endAll player_info_list = {2}  info = {3}".format(self.roomID, self.current_round, player_info_list, info))

		for p in self.players_list:
			if p and p.mb:
				p.mb.finalResult(player_info_list, info)
				if self.room_type == const.CLUB_ROOM:
					# 有效圈数加一
					p.mb.addGameCount()

		# self.end_record_game(info)
		self.saveRoomResult()
		self._reset()

	def subtotal_result(self):
		self.dismiss_timer = None
		player_info_list = [p.get_final_client_dict() for p in self.players_list if p is not None]
		DEBUG_MSG("room:{0},curround:{1} subtotal_result,player_info_list:{2}".format(self.roomID, self.current_round, player_info_list))

		for p in self.players_list:
			if p and p.mb:
				try:
					p.mb.subtotalResult(player_info_list)
				except:
					pass
		self._reset()

	def doOperation(self, avt_mb, aid, tile_list):
		idx = -1
		for i, p in enumerate(self.players_list):
			if p and p.mb == avt_mb:
				idx = i
		# tile = tile_list[0]

		DEBUG_MSG("room:{0},curround:{1} idx:{2} doOperation current_idx:{3} aid:{4} tile_list:{5}".format(self.roomID, self.current_round, idx, self.current_idx, aid, tile_list))
		"""
		当前控牌玩家摸牌后向服务端确认的操作
		"""
		if self.dismiss_room_ts != 0 and int(time.time() - self.dismiss_room_ts) < const.DISMISS_ROOM_WAIT_TIME:
			# 说明在准备解散投票中,不能进行其他操作
			DEBUG_MSG("room:{0},curround:{1} idx:{2} doOperationFailed dismiss_room_ts:{3}".format(self.roomID, self.current_round, idx, self.dismiss_room_ts))
			avt_mb.doOperationFailed(const.OP_ERROR_VOTE)
			return
		if self.state != const.ROOM_PLAYING:
			DEBUG_MSG("room:{0},curround:{1} idx:{2} doOperationFailed state:{3}".format(self.roomID, self.current_round, idx, self.state))
			avt_mb.doOperationFailed(const.OP_ERROR_STATE)
			return

		# DEBUG_MSG("doOperation idx:{0},self.current_idx:{1},self.wait_op_info_list:{2}".format(idx, self.current_idx, self.wait_op_info_list))
		if len(tile_list) == 0 and aid == const.OP_DISCARD:
			avt_mb.doOperationFailed(const.OP_ERROR_ILLEGAL)
			return
		if idx != self.current_idx:
			avt_mb.doOperationFailed(const.OP_ERROR_NOT_CURRENT)
			return
		p = self.players_list[idx]
		# if aid == const.OP_DISCARD and self.can_discard(idx, tile):
		# 	self.all_discard_tiles.append(tile)
		# 	p.discardTile(tile)
		# elif aid == const.OP_CONCEALED_KONG and self.can_concealed_kong(idx, tile):
		# 	p.concealedKong(tile)
		# elif aid == const.OP_KONG_WREATH and self.can_kong_wreath(p.tiles, tile):
		# 	p.kongWreath(tile)
		# elif aid == const.OP_CONTINUE_KONG and self.can_continue_kong(idx, tile):
		# 	p.continueKong(tile)
		# elif aid == const.OP_PASS:
		# 	# 自己摸牌的时候可以杠或者胡时选择过, 则什么都不做. 继续轮到该玩家打牌.
		# 	pass
		# elif aid == const.OP_DRAW_WIN: #普通自摸胡
		# 	is_win, score, result = self.can_win(list(p.tiles), p.last_draw, const.OP_DRAW_WIN, idx)
		# 	DEBUG_MSG("room:{0},curround:{1} idx:{2} do OP_DRAW_WIN==>{3}, {4}, {5}".format(self.roomID, self.current_round, idx, is_win, score, result))
		# 	if is_win:
		# 		p.draw_win(tile, score, result)
		# 	else:
		# 		avt_mb.doOperationFailed(const.OP_ERROR_ILLEGAL)
		# 		self.current_idx = self.nextIdx
		# 		self.beginRound()
		# elif aid == const.OP_WREATH_WIN: #自摸8张花胡
		# 	is_win, score, result = self.can_win(list(p.tiles), p.last_draw, const.OP_WREATH_WIN, idx)
		# 	DEBUG_MSG("room:{0},curround:{1} idx:{2} do OP_WREATH_WIN==>{3}, {4}, {5}".format(self.roomID, self.current_round, idx, is_win, score, result))
		# 	if is_win:
		# 		p.draw_win(tile, score, result)
		# 	else:
		# 		avt_mb.doOperationFailed(const.OP_ERROR_ILLEGAL)
		# 		self.current_idx = self.nextIdx
		# 		self.beginRound()
		# else:
		# 	avt_mb.doOperationFailed(const.OP_ERROR_ILLEGAL)
		# 	self.current_idx = self.nextIdx
		# 	self.beginRound()
		self.deskPokerList[idx] = tile_list
		curtile_list = utility.rightShiftCards(tile_list)
		last_list = utility.rightShiftCards(self.controller_discard_list)
		for i in range(self.player_num):
			self.current_idx = self.nextIdx
			if self.current_idx in self.win_list:
				self.deskPokerList[self.current_idx] = []
				continue
			else:
				break
		DEBUG_MSG("room:{0},curround:{1} idx:{2} curtile_list:{3} last_list:{4} curPrizeList{5}".format(self.roomID, self.current_round, idx, curtile_list, last_list, self.curPrizeList))
		if aid == const.OP_DISCARD and utility.compareTile(curtile_list, last_list):
			self.curround_score += utility.getDiscardScore(curtile_list)
			bombScore = utility.getBombScore(curtile_list)
			if bombScore > 0:
				self.cal_score(idx, self.win_list, const.OP_DISCARD, bombScore)
				self.curPrizeList[idx] += int(bombScore / 30)
			if len(self.round_end_list) != len(self.win_list):
				end_idx = self.win_list[len(self.win_list) - 1]
				self.round_end_list.append(end_idx)
			p.discardTile(tile_list)
		elif aid == const.OP_PASS:
			# 客户端判定没有大过上家的牌
			self.op_pass_flag += 1
			if self.op_pass_flag == self.player_num - len(self.round_end_list) - 1:
				# 这里是其他人都要不起，再次轮到自己出牌
				self.controller_discard_list = []
				self.round_start = True
				if len(self.round_end_list) == len(self.win_list):
					self.players_list[self.current_idx].add_score(self.curround_score)
					self.curScoreList[self.current_idx] += self.curround_score
				else:
					end_idx = self.win_list[len(self.win_list) - 1]
					self.curScoreList[end_idx] += self.curround_score
					self.players_list[end_idx].add_score(self.curround_score)
					self.round_end_list.append(end_idx)
				self.curround_score = 0
			# elif self.op_pass_flag == self.player_num - len(self.win_list) - 1:
			# 	end_idx = self.win_list[len(self.win_list) - 1]
			# 	self.round_end_list.append(end_idx)
			# 	self.controller_discard_list = []
			# 	self.round_start = True
			# 	self.curScoreList[self.current_idx] += self.curround_score
			# 	self.players_list[self.current_idx].add_score(self.curround_score)
			# 	self.curround_score = 0
			DEBUG_MSG("room:{0},curround:{1} idx:{2}OP_PASS curround_score:{3}".format(self.roomID, self.current_round, idx, self.curround_score))
			def delay_callback():
				self.broadcastOperation(idx, aid, [], self.curround_score, self.curScoreList)
			self.add_timer(const.DELAY_OP_PASS, delay_callback)
		else:
			DEBUG_MSG("room:{0},curround:{1} idx:{2} doOperationFailed".format(self.roomID, self.current_round, idx))
			avt_mb.doOperationFailed(const.OP_ERROR_ILLEGAL)
			return
		if len(self.win_list) < self.player_num - 1:
			DEBUG_MSG("room:{0},curround:{1} idx:{2} curround_score:{3}".format(self.roomID, self.current_round, idx, self.curround_score))
			def delay_callback():
				self.waitForOperation(self.current_idx, const.OP_DISCARD, 0, self.round_start)
			if aid == const.OP_PASS:
				self.add_timer(const.DELAY_OP_PASS, delay_callback)
			else:
				self.waitForOperation(self.current_idx, const.OP_DISCARD, 0, self.round_start)


	def broadcastOperation(self, idx, aid, tile_list = None, curround_score = 0, curScoreList = []):
		"""
		将操作广播给所有人, 包括当前操作的玩家
		:param idx: 当前操作玩家的座位号
		:param aid: 操作id
		:param tile_list: 出牌的list
		"""
		for i, p in enumerate(self.players_list):
			if p is not None:
				p.mb.postOperation(idx, aid, tile_list, curround_score, curScoreList)

	def confirmOperation(self, avt_mb, aid, tile_list):
		tile = tile_list[0]
		idx = -1
		for i, p in enumerate(self.players_list):
			if p and p.mb == avt_mb:
				idx = i
		DEBUG_MSG("room:{0},curround:{1} idx:{2} confirmOperation aid:{3} tile_list:{4}".format(self.roomID, self.current_round, idx, aid, tile_list))
		""" 被轮询的玩家确认了某个操作 """
		if self.dismiss_room_ts != 0 and int(time.time() - self.dismiss_room_ts) < const.DISMISS_ROOM_WAIT_TIME:
			# 说明在准备解散投票中,不能进行其他操作
			return

		#玩家是否可以操作
		DEBUG_MSG("room:{0},curround:{1} idx:{2} wait_op_info_list:{3}".format(self.roomID, self.current_round, idx, self.wait_op_info_list))
		if len(self.wait_op_info_list) <= 0 or sum([1 for waitOpDict in self.wait_op_info_list if (waitOpDict["idx"] == idx and waitOpDict["state"] == const.OP_STATE_WAIT)]) <= 0:
			avt_mb.doOperationFailed(const.OP_ERROR_NOT_CURRENT)
			return
		#提交 玩家结果
		for waitOpDict in self.wait_op_info_list:
			if waitOpDict["idx"] == idx:
				if waitOpDict["aid"] == const.OP_CHOW and aid == const.OP_CHOW and waitOpDict["tileList"][0] == tile_list[0] and self.can_chow_list(waitOpDict["idx"], tile_list):
					waitOpDict["state"] = const.OP_STATE_SURE
					waitOpDict["tileList"] = tile_list
				elif waitOpDict["aid"] == aid and aid != const.OP_CHOW:
					waitOpDict["state"] = const.OP_STATE_SURE		
				else:
					waitOpDict["state"] = const.OP_STATE_PASS
		#有玩家可以操作
		isOver,confirmOpDict = self.getConfirmOverInfo()
		if isOver:
			DEBUG_MSG("room:{0},curround:{1} commit over {2}.".format(self.roomID, self.current_round, confirmOpDict))
			temp_wait_op_info_list = copy.deepcopy(self.wait_op_info_list)
			self.wait_op_info_list = []
			if len(confirmOpDict) > 0:
				sureIdx = confirmOpDict["idx"]
				p = self.players_list[sureIdx]
				if confirmOpDict["aid"] == const.OP_CHOW:
					self.current_idx = sureIdx
					p.chow(confirmOpDict["tileList"])
				elif confirmOpDict["aid"] == const.OP_PONG:
					self.current_idx = sureIdx
					p.pong(confirmOpDict["tileList"][0])
				elif confirmOpDict["aid"] == const.OP_EXPOSED_KONG:
					self.current_idx = sureIdx
					p.exposedKong(confirmOpDict["tileList"][0])
				elif confirmOpDict["aid"] == const.OP_KONG_WIN:
					p.kong_win(confirmOpDict["tileList"][0], confirmOpDict["score"], confirmOpDict["result"])
				elif confirmOpDict["aid"] == const.OP_GIVE_WIN:
					p.give_win(confirmOpDict["tileList"][0], confirmOpDict["score"], confirmOpDict["result"])
				else:
					lastAid = temp_wait_op_info_list[0]["aid"]
					if lastAid == const.OP_WREATH_WIN:
						self.current_idx = self.last_player_idx
					elif lastAid == const.OP_KONG_WIN:
						#*********没人抢杠胡 杠要算分？***********
						self.current_idx = self.last_player_idx
						if self.can_cut_after_kong():
							self.cutAfterKong()
					else:
						self.current_idx = self.nextIdx
					self.beginRound()
			else:
				lastAid = temp_wait_op_info_list[0]["aid"]
				if lastAid == const.OP_WREATH_WIN:
					self.current_idx = self.last_player_idx
				elif lastAid == const.OP_KONG_WIN:
					#*********没人抢杠胡 杠要算分？***********
					self.current_idx = self.last_player_idx
				else:
					self.current_idx = self.nextIdx
				self.beginRound()

	def getConfirmOverInfo(self):
		for i in range(len(self.wait_op_info_list)):
			waitState = self.wait_op_info_list[i]["state"]
			if waitState == const.OP_STATE_PASS:
				continue
			elif waitState == const.OP_STATE_WAIT: #需等待其他玩家操作
				return False, {}
			elif waitState == const.OP_STATE_SURE:	#有玩家可以操作
				return True, self.wait_op_info_list[i]
		return True, {}	#所有玩家选择放弃

	def waitForOperation(self, idx, aid, tile, round_start = False): #  aid抢杠 杠花没人可胡 nextIdx还是自己
		# notifyOpList = self.getNotifyOpList(idx, aid, tile)
		# if sum([len(x) for x in notifyOpList]) > 0:
		# 	DEBUG_MSG("room:{0},curround:{1} waitForOperation from:{2},aid:{3},tile:{4}==>notifyOpList:{5}".format(self.roomID, self.current_round, idx, aid, tile, notifyOpList))
		# 	for i,p in enumerate(self.players_list):
		# 		if p is not None and len(notifyOpList[i]) > 0:
		# 			waitAidList = [notifyOp["aid"] for notifyOp in notifyOpList[i]]
		# 			p.mb.waitForOperation(waitAidList, [tile,])
		# else:
		# 	DEBUG_MSG("room:{0},curround:{1} nobody waitForOperation from:{2},aid:{3},tile:{4},nextIdx:{5}".format(self.roomID, self.current_round, idx, aid, tile, nextIdx))
		# 	if self.can_cut_after_kong() and (aid >> 3) == const.SHOW_KONG:
		# 		self.cutAfterKong()
		# 	self.current_idx = self.nextIdx if nextIdx < 0 else nextIdx
		# 	self.beginRound()
		DEBUG_MSG("room:{0},curround:{1} waitForOperation idx:{2},aid:{3},round_start:{4}".format(self.roomID, self.current_round, idx, aid, round_start))
		self.waitIdx = self.current_idx
		for i,p in enumerate(self.players_list):
			if p is not None:
				p.mb.waitForOperation(idx, aid, round_start)
		if len(self.getTipsCards()) == 0 and self.players_list[self.current_idx].online and not round_start:
			self.players_list[self.current_idx].mb.doOperation(const.OP_PASS, [])

	def get_init_client_dict(self):
		return {
			'roomID'			: self.roomID,
			'ownerId'			: self.owner_uid,
			'roomType'			: self.room_type,
			'dealerIdx'			: self.dealer_idx,
			'curRound'			: self.current_round,
			'maxRound'			: self.game_round,
			'player_num'		: self.player_num,
			# 'king_num'			: self.king_num,
			'pay_mode'			: self.pay_mode,
			# 'game_mode'			: self.game_mode,
			# 'game_max_lose'		: self.game_max_lose,
			# 'round_max_lose'	: self.round_max_lose,
			# 'lucky_num'			: self.lucky_num,
			'hand_prepare'		: self.hand_prepare,
			# 'base_score'		: self.base_score,
			# 'king_mode'			: self.king_mode,
			# 'begin_dealer_mul'	: self.begin_dealer_mul,
			# 'cur_dealer_mul'		: self.cur_dealer_mul,
			# 'win_mode'			: self.win_mode,
			# 'three_job'			: self.three_job,
			# 'pong_useful'		: self.pong_useful,
			# 'bao_tou'			: self.bao_tou,
			'club_id'			: self.club.clubId if self.club is not None else 0,
			'player_base_info_list': [p.get_init_client_dict() for p in self.players_list if p is not None],
			'player_state_list': [1 if i in self.confirm_next_idx else 0 for i in range(const.ROOM_PLAYER_NUMBER)],
		}

	def get_agent_client_dict(self):
		return {
			'roomID'			: self.roomID,
			'curRound'			: self.current_round,
			# 'cur_dealer_mul'	: self.cur_dealer_mul,
			'maxRound'			: self.game_round,
			# 'king_num'			: self.king_num,
			'pay_mode'			: self.pay_mode,
			# 'game_mode'			: self.game_mode,
			# 'game_max_lose'		: self.game_max_lose,
			# 'round_max_lose'	: self.round_max_lose,
			'player_num'		: self.player_num,
			# 'base_score'		: self.base_score,
			# 'king_mode'			: self.king_mode,
			# 'win_mode'			: self.win_mode,
			# 'three_job'			: self.three_job,
			# 'pong_useful'		: self.pong_useful,
			# 'bao_tou'			: self.bao_tou,
			# 'begin_dealer_mul'	: self.begin_dealer_mul,
			# 'lucky_num'			: self.lucky_num,
			'hand_prepare'		: self.hand_prepare,
			'player_simple_info_list': [p.get_simple_client_dict() for p in self.players_list if p is not None]
		}

	def get_agent_complete_dict(self):
		return {
			'roomID'			: self.roomID,
			'maxRound'			: self.game_round,
			# 'king_num'			: self.king_num,
			# 'game_mode'			: self.game_mode,
			# 'game_max_lose'		: self.game_max_lose,
			# 'round_max_lose'	: self.round_max_lose,
			'player_num'		: self.player_num,
			'pay_mode'			: self.pay_mode,
			# 'base_score'		: self.base_score,
			# 'king_mode'			: self.king_mode,
			# 'win_mode'			: self.win_mode,
			# 'three_job'			: self.three_job,
			# 'pong_useful'		: self.pong_useful,
			# 'bao_tou'			: self.bao_tou,
			# 'begin_dealer_mul'	: self.begin_dealer_mul,
			# 'lucky_num'			: self.lucky_num,
			'hand_prepare'		: self.hand_prepare,
			'time'				: utility.get_cur_timestamp(),
			'player_simple_info_list': [p.get_simple_client_dict() for p in self.origin_players_list if p is not None],
		}

	def get_club_complete_dict(self):
		return {
			'roomID'		: self.roomID,
			'time'			: utility.get_cur_timestamp(),
			'player_info_list': [p.get_club_client_dict() for p in self.origin_players_list if p is not None],
		}

	def get_reconnect_room_dict(self, userId):
		dismiss_left_time =const.DISMISS_ROOM_WAIT_TIME - (int(time.time() - self.dismiss_room_ts))
		if self.dismiss_room_ts == 0 or dismiss_left_time >= const.DISMISS_ROOM_WAIT_TIME:
			dismiss_left_time = 0

		idx = 0
		for p in self.players_list:
			if p and p.userId == userId:
				idx = p.idx

		waitAidList = []
		for i in range(len(self.wait_op_info_list)):
			if self.wait_op_info_list[i]["idx"] == idx and self.wait_op_info_list[i]["state"] == const.OP_STATE_WAIT:
				waitAidList.append(self.wait_op_info_list[i]["aid"])
		DEBUG_MSG('room:{},curround:{} reconnect_room waitAidList:{}'.format(self.roomID, self.current_round, waitAidList))
		DEBUG_MSG("current_idx:{} controller_discard_list:{} deskPokerList:{} curround_score:{}".format(self.current_idx, self.controller_discard_list, self.deskPokerList, self.curround_score))
		return {
			'init_info' 				: self.get_init_client_dict(),
			'controllerIdx'				: self.current_idx,
			'controller_discard_list'	: self.controller_discard_list,
			'curround_score'			: self.curround_score,
			'deskPokerList'				: self.deskPokerList,
			'waitIdx'					: self.current_idx,
			'room_state'				: const.ROOM_PLAYING if self.state == const.ROOM_PLAYING else const.ROOM_WAITING,
			'win_list'					: self.win_list,
			'curPrizeList'				: self.curPrizeList,
			'curScoreList'				: self.curScoreList,
			'player_state_list'			: [1 if i in self.confirm_next_idx else 0 for i in range(self.player_num)],
			'applyCloseFrom'			: self.dismiss_room_from,
			'applyCloseLeftTime'		: dismiss_left_time,
			'applyCloseStateList'		: self.dismiss_room_state_list,
			'player_advance_info_list'	: [p.get_reconnect_client_dict(userId) for p in self.players_list if p is not None],
		}

	def broadcastEnterRoom(self, idx):
		new_p = self.players_list[idx]
		for i, p in enumerate(self.players_list):
			if p is None:
				continue
			if i == idx:
				p.mb.enterRoomSucceed(self, idx)
			else:
				p.mb.othersEnterRoom(new_p.get_init_client_dict())

	def record_round_result(self):
		# 玩家记录当局战绩
		d = datetime.fromtimestamp(time.time())
		round_result_d = {
			'date': '-'.join([str(d.year), str(d.month), str(d.day)]),
			'time': ':'.join([str(d.hour), str(d.minute)]),
			'round_record': [p.get_round_result_info() for p in self.players_list if p],
			'recordId': self.record_id
		}
		self.game_result['round_result'].append(round_result_d)
		
	def begin_record_room(self):
		# 在第一局的时候记录基本信息
		if self.current_round != 1:
			return
		self.game_result = {
			'maxRound': self.game_round,
			# 'gameMaxLose': self.game_max_lose,
			'roomID': self.roomID,
			'user_info_list': [p.get_basic_user_info() for p in self.players_list if p]
		}
		self.game_result['round_result'] = []

	def save_game_result(self):
		DEBUG_MSG('room:{},curround:{}  len:{} {}'.format(self.roomID, self.current_round, len(self.game_result.get('round_result', [])), "-save-" * 10))
		if len(self.game_result['round_result']) > 0:
			result_str = json.dumps(self.game_result)
			for p in self.players_list:
				p and p.save_game_result(result_str)

	def save_agent_complete_result(self):
		DEBUG_MSG('room:{},curround:{} ------ save agent complete result -----'.format(self.roomID, self.current_round))
		d = self.get_agent_complete_dict()
		result_str = json.dumps(d)
		if self.agent:
			if self.agent.isDestroyed:
				import x42
				for k, v in x42.GW.avatars.items():
					if v.userId == self.agent.userId:
						v.saveAgentRoomResult(result_str)
						break
				else:
					ERROR_MSG("room:{},curround:{} Save AgentRoom result failed!!! agent.userId = {}".format(self.roomID, self.current_round, self.agent.userId))
			else:
				self.agent.saveAgentRoomResult(result_str)

	def save_club_result(self):
		DEBUG_MSG('room:{},curround:{} ------ save club result -----'.format(self.roomID, self.current_round))
		d = self.get_club_complete_dict()
		if self.club:
			self.club.saveTableResult(d)

	def saveRoomResult(self):
		# 保存玩家的战绩记录
		self.save_game_result()
		# 保存代理开房的记录
		if self.room_type == const.AGENT_ROOM and self.agent:
			# 代理开房完成记录
			self.save_agent_complete_result()
			# 将房间从代理房间中删除
			self.agent.agentRoomDropped(self.roomID)
		# 保存茶楼的战绩
		if self.room_type == const.CLUB_ROOM:
			self.save_club_result()

	def timeoutDestroy(self):
		INFO_MSG("room:{},curround:{} timeout destroyed. room_type = {}, owner_uid = {}".format(self.roomID, self.current_round, self.room_type, self.owner_uid))
		if self.current_round < 1:
			self.do_drop_room()

	def destroySelf(self):
		self.clear_timers()
		not self.isDestroyed and self.destroy()

	def destroyByServer(self, reason=None):
		# 此接口由GameWorld关服时调用
		self.dismiss_timer = None
		for p in self.players_list:
			if p and p.mb:
				try:
					p.mb.quitRoomSucceed()
					if reason:
						p.mb.showTip(reason)
				except:
					pass

		self.destroySelf()


	def getSeatAbstractInfo(self):
		seat = 0
		for i in range(const.ROOM_PLAYER_NUMBER):
			p = self.players_list[i]
			if p:
				seat |= 2 ** i
		return seat

	def getSeatDetailInfo(self):
		detail = []
		for p in self.players_list:
			if p:
				detail.append(p.get_simple_client_dict())
		return detail

	def getCalCostNeed(self):
		return {
			'pay_mode' : self.pay_mode,
		}