import gym
import math
import random
import os
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from collections import namedtuple
from itertools import count
from copy import deepcopy
from PIL import Image
import time

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.autograd import Variable

import threading
from utils.replayBuffer import EXP,PrioritizedReplayBuffer
from utils.statsLogger import statsLogger


import torchvision.transforms as T
import logging
import copy
import random


bashlogger = logging.getLogger("bash logger")
bashlogger.setLevel(logging.DEBUG)
FORMAT = '[%(asctime)-15s][%(threadName)s][%(levelname)s][%(funcName)s] %(message)s'
logging.basicConfig(format=FORMAT)


use_cuda = False#torch.cuda.is_available()
rendering = False


FloatTensor = torch.cuda.FloatTensor if use_cuda else torch.FloatTensor
LongTensor = torch.cuda.LongTensor if use_cuda else torch.LongTensor
ByteTensor = torch.cuda.ByteTensor if use_cuda else torch.ByteTensor
Tensor = FloatTensor
dtype = torch.cuda.FloatTensor if use_cuda else torch.FloatTensor

Transition = namedtuple('Transition', ('state','action','next_state', 'reward','done') )
TransitionPR = namedtuple('TransitionPR', ('idx','priority','state','action','next_state', 'reward','done') )
class ReplayMemory(object) :
	def __init__(self,capacity) :
		self.capacity = capacity
		self.memory = []
		self.position = 0

	def push(self, *args) :
		if len(self.memory) < self.capacity :
			self.memory.append(None)
		self.memory[self.position] = Transition(*args)
		self.position = (self.position+1) % self.capacity
		self.position = int(self.position)

	def sample(self,batch_size) :
		return random.sample(self.memory, batch_size)

	def __len__(self) :
		return len(self.memory)



class DQN(nn.Module) :
	def __init__(self,nbr_actions=2) :
		super(DQN,self).__init__()
		self.nbr_actions = nbr_actions

		self.conv1 = nn.Conv2d(3,16, kernel_size=5, stride=2)
		self.bn1 = nn.BatchNorm2d(16)
		self.conv2 = nn.Conv2d(16,32, kernel_size=5, stride=2)
		self.bn2 = nn.BatchNorm2d(32)
		self.conv3 = nn.Conv2d(32,32, kernel_size=5, stride=2)
		self.bn3 = nn.BatchNorm2d(32)
		#self.head = nn.Linear(448,self.nbr_actions)
		self.head = nn.Linear(192,self.nbr_actions)

	def forward(self, x) :
		x = F.relu( self.bn1(self.conv1(x) ) )
		x = F.relu( self.bn2(self.conv2(x) ) )
		x = F.relu( self.bn3(self.conv3(x) ) )
		x = x.view( x.size(0), -1)
		x = self.head( x )
		return x

class DQN_HER(nn.Module) :
	def __init__(self,nbr_actions=2) :
		super(DQN_HER,self).__init__()
		self.nbr_actions = nbr_actions

		self.conv1 = nn.Conv2d(3*2,16, kernel_size=5, stride=2)
		self.bn1 = nn.BatchNorm2d(16)
		self.conv2 = nn.Conv2d(16,32, kernel_size=5, stride=2)
		self.bn2 = nn.BatchNorm2d(32)
		self.conv3 = nn.Conv2d(32,32, kernel_size=5, stride=2)
		self.bn3 = nn.BatchNorm2d(32)
		#self.head = nn.Linear(2464,self.nbr_actions)
		#self.head = nn.Linear(480,self.nbr_actions)
		#self.head = nn.Linear(192,self.nbr_actions)
		self.head = nn.Linear(64,self.nbr_actions)
		#self.head = nn.Linear(12512,self.nbr_actions)

	def forward(self, x) :
		x = F.relu( self.bn1(self.conv1(x) ) )
		x = F.relu( self.bn2(self.conv2(x) ) )
		x = F.relu( self.bn3(self.conv3(x) ) )
		x = x.view( x.size(0), -1)
		x = self.head( x )
		return x

def sample_goal(buffer_exp,strategy='final') :
	'''
	params :
		- buffer_exp : buffer of EXP object from which we will sample according to the strategy
		- strategy : 'final' / IDX(int) :
			- 'final' : returns last state of the buffer...
			- IDX(int) : isinstance(int,strategy) returns any state in buffer_exp[IDX:]
	'''
	if isinstance(strategy,int) :
		buff = buffer_exp[strategy:]
	else :
		buff = buffer_exp[-2:]

	return random.choice(buff).state 

def sample_init_goal(memory) :
	buffer_exp = memory.get_buffer()
	shape = buffer_exp[0].state.size()
	goalb = int(shape[1]/2)
	retstate = random.choice(buffer_exp).state
	ret = retstate[:,goalb:].view((1,goalb,shape[2],shape[3]))
	del buffer_exp
	return ret

def reward_function(state,goal,eps=1e-1) :
	#diff = torch.abs(state-goal).mean()
	diff = torch.abs(state-goal).max()
	#bashlogger.debug(diff)
	val = -(diff > eps)
	#bashlogger.debug(val)
	
	return torch.ones(1)*float(val) 




class DuelingDQN(nn.Module) :
	def __init__(self,nbr_actions=2) :
		super(DuelingDQN,self).__init__()
		self.nbr_actions = nbr_actions

		self.conv1 = nn.Conv2d(3,16, kernel_size=5, stride=2)
		self.bn1 = nn.BatchNorm2d(16)
		self.conv2 = nn.Conv2d(16,32, kernel_size=5, stride=2)
		self.bn2 = nn.BatchNorm2d(32)
		self.conv3 = nn.Conv2d(32,32, kernel_size=5, stride=2)
		self.bn3 = nn.BatchNorm2d(32)
		self.f = nn.Linear(192,128)
		self.value = nn.Linear(128,1)
		self.advantage = nn.Linear(128,self.nbr_actions)
		

	def forward(self, x) :
		x = F.relu( self.bn1(self.conv1(x) ) )
		x = F.relu( self.bn2(self.conv2(x) ) )
		x = F.relu( self.bn3(self.conv3(x) ) )
		x = x.view( x.size(0), -1)
		
		fx = self.f(x)

		v = self.value(fx)
		va = torch.cat( [ v for i in range(self.nbr_actions) ], dim=1)
		a = self.advantage(fx)

		suma = torch.mean(a,dim=1,keepdim=True)
		suma = torch.cat( [ suma for i in range(self.nbr_actions) ], dim=1)
		
		x = va+a-suma
		
			
		return x



def get_screen(env,action,preprocess) :
	screen, reward, done, info = env.step(action)
	screen = screen.transpose( (2,0,1) )
	screen = np.ascontiguousarray( screen, dtype=np.float32) / 255.0
	screen = torch.from_numpy(screen)
	screen = preprocess(screen)
	screen = screen.unsqueeze(0)
	#screen = screen.type(Tensor)
	return screen, reward, done, info

def get_screen_reset(env,preprocess) :
	screen = env.reset()
	screen = screen.transpose( (2,0,1) )
	screen = np.ascontiguousarray( screen, dtype=np.float32) / 255.0
	screen = torch.from_numpy(screen)
	screen = preprocess(screen)
	screen = screen.unsqueeze(0)
	return screen

def test1() :
	plt.figure()
	plt.imshow( get_screen(env.action_space.sample()).cpu().squeeze(0).permute( 1, 2 ,0 ).numpy(), interpolation='none')
	plt.title('Example extracted screen')
	plt.show()


def select_action(model,state,steps_done=[],epsend=0.05,epsstart=0.9,epsdecay=200) :
	global nbr_actions
	sample = random.random()
	if steps_done is [] :
		steps_done.append(0)

	eps_threshold = epsend + (epsstart-epsend) * math.exp(-1.0 * steps_done[0] / epsdecay )
	steps_done[0] +=1

	if sample > eps_threshold :
		return model( Variable(state, volatile=True).type(FloatTensor) ).data.max(1)[1].view(1,1)
	else :
		return LongTensor( [[random.randrange(nbr_actions) ] ] )


def plot_durations() :
	plt.figure(2)
	plt.clf()
	durations_t = torch.FloatTensor(episode_durations)
	plt.title('Training...')
	plt.xlabel('Episode')
	plt.ylabel('Duration')
	plt.plot(durations_t.numpy())
	# Take 100 episode averages and plot them too
	if len(durations_t) >= 100:
	    means = durations_t.unfold(0, 100, 1).mean(1).view(-1)
	    means = torch.cat((torch.zeros(99), means))
	    plt.plot(means.numpy())

	plt.pause(0.001)  # pause a bit so that plots are updated
	if is_ipython:
	    display.clear_output(wait=True)
	    display.display(plt.gcf())

def optimize_model(model,model_,memory,optimizer) :
	model.train()
	model_.eval()

	try :
		global last_sync
		global use_cuda
		global MIN_MEMORY
		global nbr_actions
		
		if len(memory) < MIN_MEMORY :
			return
		
		#Create Batch with PR :
		prioritysum = memory.total()
		randexp = np.random.random(size=BATCH_SIZE)*prioritysum
		batch = list()
		for i in range(BATCH_SIZE):
			try :
				el = memory.get(randexp[i])
				batch.append(el)
			except TypeError as e :
				continue
				#print('REPLAY BUFFER EXCEPTION...')
		
		batch = TransitionPR( *zip(*batch) )
		
		# Create Batch with replayMemory :
		#transitions = memory.sample(BATCH_SIZE)
		#batch = Transition(*zip(*transitions) )

		next_state_batch = Variable(torch.cat( batch.next_state), requires_grad=False)
		state_batch = Variable( torch.cat( batch.state) , requires_grad=False)
		action_batch = Variable( torch.cat( batch.action) , requires_grad=False)
		reward_batch = Variable( torch.cat( batch.reward ), requires_grad=False )
		
		if use_cuda :
			next_state_batch = next_state_batch.cuda()
			state_batch = state_batch.cuda()
			action_batch = action_batch.cuda()
			reward_batch = reward_batch.cuda()

		state_action_values = model(state_batch)
		#state_action_values_g = state_action_values.gather(1,action_batch)
		
		#next_state_values = model(non_final_next_states)
		next_state_values = model(next_state_batch)
		data  = next_state_values.max(1)
		argmax_a_next_state_values = data[1]
		max_a_next_state_values = data[0]
		
		#TODO : find the correct way to index :
		#next_state_values__argmax_a = model_(next_state_batch)[:,argmax_a_next_state_values]
		#next_state_values__argmax_a = model_(next_state_batch)[:,argmax_a_next_state_values.cpu().data]
		next_state_values_ = model_(next_state_batch)
		np_argmax = list(argmax_a_next_state_values.cpu().data.view((-1)))
		snext_state_values_ = torch.cat( [ next_state_values_[i,argmax] for i,argmax in enumerate(np_argmax) ] ).view((-1,1))
		next_state_values__argmax_a = torch.cat( [ snext_state_values_ for i in range(nbr_actions) ], dim=1)
		
		
		# Compute the expected Q values
		expected_state_action_values = (next_state_values__argmax_a * GAMMA) + reward_batch

		# Compute Huber loss
		#loss = F.smooth_l1_loss(state_action_values_g, expected_state_action_values)
		loss = F.smooth_l1_loss(state_action_values, expected_state_action_values)
		loss_np = loss.cpu().data.numpy()
	
		# Optimize the model
		optimizer.zero_grad()
		loss.backward()
		
	except Exception as e :
		#TODO : find what is the reason of this error in backward...
		#"leaf variable was used in an inplace operation."
		bashlogger.exception('Error in optimizer_model : {}'.format(e) )
		
	for param in model.parameters():
	    if param.grad is not None :
	    	if param.grad.data is not None :
	    		param.grad.data.clamp_(-1, 1)

	optimizer.step()

	#UPDATE THE PR :
	for (idx, new_error) in zip(batch.idx,loss_np) :
		new_priority = memory.priority(new_error)
		#print( 'prior = {} / {}'.format(new_priority,self.rBuffer.total()) )
		memory.update(idx,new_priority)

	del batch 
	del next_state_batch
	del state_batch
	del action_batch
	del reward_batch
	
	return loss_np

def optimize_model1(model,model_,memory,optimizer) :
	model.train()
	try :
		global last_sync
		global use_cuda
		global MIN_MEMORY
		global nbr_actions
		
		if len(memory) < MIN_MEMORY :
			return
		
		#Create Batch with PR :
		prioritysum = memory.total()
		randexp = np.random.random(size=BATCH_SIZE)*prioritysum
		batch = list()
		for i in range(BATCH_SIZE):
			try :
				el = memory.get(randexp[i])
				batch.append(el)
			except TypeError as e :
				continue
				#print('REPLAY BUFFER EXCEPTION...')
		
		batch = TransitionPR( *zip(*batch) )
		
		# Create Batch with replayMemory :
		#transitions = memory.sample(BATCH_SIZE)
		#batch = Transition(*zip(*transitions) )

		#non_final_mask = ByteTensor( tuple(map(lambda s: s is not None, batch.next_state) ) )
		# We don't want to backprop through the expected action values and volatile
		# will save us on temporarily changing the model parameters'
		# requires_grad to False!
		#non_final_next_states = Variable(torch.cat([s for s in batch.next_state
		#                                        if s is not None]),
		#                             volatile=True)
		next_state_batch = Variable(torch.cat( batch.next_state), requires_grad=False)
		state_batch = Variable( torch.cat( batch.state) , requires_grad=False)
		action_batch = Variable( torch.cat( batch.action) , requires_grad=False)
		reward_batch = Variable( torch.cat( batch.reward ), requires_grad=False )
		
		if use_cuda :
			#non_final_mask = non_final_mask.cuda()
			#non_final_next_states = non_final_next_states.cuda()
			next_state_batch = next_state_batch.cuda()
			state_batch = state_batch.cuda()
			action_batch = action_batch.cuda()
			reward_batch = reward_batch.cuda()

		state_action_values = model(state_batch)
		#state_action_values_g = state_action_values.gather(1,action_batch)
		
		# Compute V(s_{t+1}) for all next states.
		'''
		next_state_values = Variable(torch.zeros(BATCH_SIZE))#.type(Tensor))
		if use_cuda :
			nex_state_values = next_state_values.cuda()

		next_state_values[non_final_mask.cpu()] = model(non_final_next_states).cpu()
		print(next_state_values)
		next_state_values = next_state_values.max(1)[0]
		'''
		#next_state_values = Variable(torch.zeros(BATCH_SIZE))#.type(Tensor))
		#if use_cuda :
		#	nex_state_values = next_state_values.cuda()

		#next_state_values = model(non_final_next_states)
		next_state_values = model(next_state_batch)
		data  = next_state_values.max(1)
		argmax_a_next_state_values = data[1]
		max_a_next_state_values = data[0]
		
		#TODO : find the correct way to index :
		#next_state_values__argmax_a = model_(next_state_batch)[:,argmax_a_next_state_values]
		#next_state_values__argmax_a = model_(next_state_batch)[:,argmax_a_next_state_values.cpu().data]
		next_state_values_ = model_(next_state_batch)
		np_argmax = list(argmax_a_next_state_values.cpu().data.view((-1)))
		snext_state_values_ = torch.cat( [ next_state_values_[i,argmax] for i,argmax in enumerate(np_argmax) ] ).view((-1,1))
		next_state_values__argmax_a = torch.cat( [ snext_state_values_ for i in range(nbr_actions) ], dim=1)
		
		
		# Now, we don't want to mess up the loss with a volatile flag, so let's
		# clear it. After this, we'll just end up with a Variable that has
		# requires_grad=False
		#next_state_values.volatile = False
		# Compute the expected Q values
		expected_state_action_values = (next_state_values__argmax_a * GAMMA) + reward_batch

		# Compute Huber loss
		#loss = F.smooth_l1_loss(state_action_values_g, expected_state_action_values)
		loss = F.smooth_l1_loss(state_action_values, expected_state_action_values)
		loss_np = loss.cpu().data.numpy()
	
		# Optimize the model
		optimizer.zero_grad()
		loss.backward()
		
	except Exception as e :
		#TODO : find what is the reason of this error in backward...
		#"leaf variable was used in an inplace operation."
		bashlogger.exception('Error in optimizer_model : {}'.format(e) )
		
	for param in model.parameters():
	    if param.grad is not None :
	    	if param.grad.data is not None :
	    		param.grad.data.clamp_(-1, 1)
	optimizer.step()

	#UPDATE THE PR :
	for (idx, new_error) in zip(batch.idx,loss_np) :
		new_priority = memory.priority(new_error)
		#print( 'prior = {} / {}'.format(new_priority,self.rBuffer.total()) )
		memory.update(idx,new_priority)

	del batch 

	return loss_np
def render(frame) :
	if use_cuda :
		plt.imshow( frame.cpu().squeeze(0).permute( 1, 2 ,0 ).numpy(), interpolation='none')
	else :
		plt.imshow( frame.squeeze(0).permute( 1, 2 ,0 ).numpy(), interpolation='none')
	plt.title('Current Frame')
	plt.pause(1e-4)

class Worker :
	def __init__(self,index,model,env,memory,lr=1e-2,preprocess=T.ToTensor(),path=None,frompath=None,num_episodes=1000,epsend=0.05,epsstart=0.9,epsdecay=200,k=4,strategy='future') :
		self.index = index
		self.model = model
		self.envstr = env
		self.env = gym.make(self.envstr)
		self.env.reset()

		self.memory = memory
		self.lr = lr

		#self.optimizer = optim.RMSprop(self.model.parameters(), lr=self.lr )
		self.optimizer = optim.Adam(self.model.parameters(), lr=self.lr,eps=1.5e-4 )
		bashlogger.info('Optimizer {}: ok.'.format(self.index) )

		self.preprocess = preprocess
		self.path = path
		self.frompath = frompath
		self.num_episodes = num_episodes
		self.epsend = epsend
		self.epsstart = epsstart
		self.epsdecay = epsdecay

		#HER params :
		self.k = k
		self.strategy = strategy

		self.sl = statsLogger(path=self.path,filename='logs{}.csv'.format(self.index) )
		self.workerfn = lambda: train(model=self.model,
										env=self.env,
										memory=self.memory,
										optimizer=self.optimizer,
										logger=self.sl,
										preprocess=self.preprocess,
										path=self.path,
										frompath=self.frompath,
										num_episodes=self.num_episodes,
										epsend=self.epsend,
										epsstart=self.epsstart,
										epsdecay=self.epsdecay,
										k=self.k,
										strategy=self.strategy)

		self.thread = threading.Thread(target=self.workerfn)

	def start(self) :
		self.thread.start()

	def join(self) :
		self.thread.join()
	

def accumulateMemory(memory,env,model,preprocess,epsstart,epsend,epsdecay,k=4,strategy='future') :
	try :
		#exploration counter ;
		steps_done = [0]
		global MIN_MEMORY
		i = 0
		while len(memory) < MIN_MEMORY :
			bashlogger.info('Episode : {} : memory : {}/{}'.format(i,len(memory),memory.capacity) )
			i+=1
			cumul_reward = 0.0
			last_screen = get_screen_reset(env,preprocess=preprocess)
			current_screen, reward, done, info = get_screen(env,env.action_space.sample(),preprocess=preprocess )
			state = current_screen - last_screen
			episode_buffer = []

			init_goal = state
			init_goal = torch.zeros(current_screen.size())
			'''
			if len(memory) :
				init_goal = sample_init_goal(memory)
			'''

			for t in count() :
				model.eval()
				#HER :
				stategoal = torch.cat( [state,init_goal], dim=1)

				#action = select_action(model,state,steps_done=steps_done,epsend=epsend,epsstart=epsstart,epsdecay=epsdecay)
				action = select_action(model,stategoal,steps_done=steps_done,epsend=epsend,epsstart=epsstart,epsdecay=epsdecay)
				last_screen = current_screen
				current_screen, reward, done, info = get_screen(env,action[0,0],preprocess=preprocess)
				cumul_reward += reward

				reward = Tensor([reward])

				if not done :
					next_state = current_screen -last_screen
				else :
					next_state = torch.ones(current_screen.size())

				episode_buffer.append( EXP(state,action,next_state,reward,done) )

				state = next_state

				if done :
					log = 'Episode duration : {}'.format(t+1) +'---' +' Reward : {}'.format(cumul_reward)
					bashlogger.info(log)
					break


			#Let us add this episode_buffer to the replayBuffer :
			for itexp in range(len(episode_buffer)) :
				el = episode_buffer[itexp]
				#HER : reward with init_goal
				reward = reward_function(el.state,init_goal)+el.reward
				
				#store this transition with init_goal :
				init_el = EXP( state=torch.cat( [el.state, init_goal], dim=1),
								action=el.action,
								next_state=torch.cat( [el.next_state, init_goal], dim=1),
								reward=reward,
								done=el.done
							)
				
				init_priority = memory.priority( torch.abs(init_el.reward).numpy() )
				
				memory.add(init_el,init_priority)

				#store for multiple goals :
				#1: sample new goal :
				goals = []
				for j in range(k) :
					goal = None
					if strategy == 'final' :
						goal = sample_goal(episode_buffer, strategy=strategy)
					elif strategy == 'future' :
						goal = sample_goal(episode_buffer, strategy=i)	
					goals.append(goal)
					

				#For each goal ...
				for goal in goals :
					#2: .. compute reward :
					goalreward = reward_function(el.state,goal)+el.reward
					#3: ... store this transistion with goal :
					goalel = EXP( state=torch.cat( [el.state, goal], dim=1),
									action=el.action,
									next_state = torch.cat( [el.next_state, goal], dim=1),
									reward = goalreward,
									done=el.done
								)
					
					init_priority = memory.priority( torch.abs(goalel.reward).numpy() )
					memory.add(goalel,init_priority)
					
				del el
				del goals
			
			del episode_buffer

		bashlogger.info('MEMORY : initialization : complete.')
	except Exception as e :
		bashlogger.exception(e)
		

#@profile
def train(model,env,memory,optimizer,logger=None,preprocess=T.ToTensor(),path=None,frompath=None,num_episodes=1000,epsend=0.05,epsstart=0.9,epsdecay=200,k=4,strategy='future',singlegoal=False): 
	try :
		episode_durations = []
		episode_reward = []
		episode_loss = []
		global rendering
		global use_cuda
		#exploration counter ;
		steps_done = [0]
		
		#Double Network initialization :
		savemodel(model,path+'.save')
		#model_ = DuelingDQN(model.nbr_actions)
		#model_ = DQN(model.nbr_actions)
		model_ = DQN_HER(model.nbr_actions)
		if use_cuda :
			model_ = model_.cuda()
		loadmodel(model_,path+'.save')
		_updateT = 10 # x count() frame
		_counterT = 0

		accumulateMemory(memory,env,model,preprocess,epsstart=0.5,epsend=0.3,epsdecay=200,k=k,strategy=strategy)

		for i in range(num_episodes) :
			bashlogger.info('Episode : {} : memory : {}/{}'.format(i,len(memory),memory.capacity) )
			cumul_reward = 0.0
			last_screen = get_screen_reset(env,preprocess=preprocess)
			current_screen, reward, done, info = get_screen(env,env.action_space.sample(),preprocess=preprocess )
			state = current_screen - last_screen
			
			episode_buffer = []
			meanfreq = 0
			episode_loss_buffer = []

			#periodically save model to model_:
			_counterT = (_counterT+1)%_updateT
			if _counterT == 0 :
				savemodel(model,path+'.save')
				loadmodel(model_,path+'.save')

			#HER : sample initial goal :
			if not singlegoal :
				init_goal = sample_init_goal(memory)
			else :
				init_goal = torch.zeros(current_screen.size())

			for t in count() :
				model.eval()
				#HER :
				stategoal = torch.cat( [state,init_goal], dim=1)

				#action = select_action(model,state,steps_done=steps_done,epsend=epsend,epsstart=epsstart,epsdecay=epsdecay)
				action = select_action(model,stategoal,steps_done=steps_done,epsend=epsend,epsstart=epsstart,epsdecay=epsdecay)
				
				last_screen = current_screen
				current_screen, reward, done, info = get_screen(env,action[0,0],preprocess=preprocess)
				cumul_reward += reward
				
				if rendering :
					render(current_screen)#env.render()
				
				reward = Tensor([reward])

				if not done :
					next_state = current_screen -last_screen
				else :
					next_state = torch.ones(current_screen.size())

				episode_buffer.append( EXP(state,action,next_state,reward,done) )

				state = next_state

				since = time.time()
				
				#lossnp = optimize_model(model,model_,memory,optimizer)
				lossnp = optimize_model1(model,model_,memory,optimizer)


				if lossnp is not None :
					episode_loss_buffer.append(  np.mean(lossnp) )
				else :
					episode_loss_buffer.append(0)


				elt = time.time() - since
				f = 1.0/elt
				meanfreq = (meanfreq*(t+1) + f)/(t+2)
				#print('{} Hz ; {} seconds.'.format(f,elt) )
				
				if done :
					episode_durations.append(t+1)
					episode_reward.append(cumul_reward)
					meanloss = np.mean(episode_loss_buffer)
					episode_loss.append(meanloss)

					log = 'Episode duration : {}'.format(t+1) +'---' +' Reward : {} // Mean Loss : {}'.format(cumul_reward,meanloss) +'---'+' {}Hz'.format(meanfreq)
					bashlogger.info(log)
					if logger is not None :
						new = {'episodes':[i],'duration':[t+1],'reward':[cumul_reward],'mean frequency':[meanfreq],'loss':[meanloss]}
						logger.append(new)

					if path is not None :
						savemodel(model,path+'.save')
						bashlogger.info('Model saved : {}'.format(path) )
					#plot_durations()
					break


			#Let us add this episode_buffer to the replayBuffer :
			for itexp in range(len(episode_buffer)) :
				el = episode_buffer[itexp]
				#HER : reward with init_goal
				reward = reward_function(el.state,init_goal)+el.reward
				
				#store this transition with init_goal :
				init_el = EXP( state=torch.cat( [el.state, init_goal], dim=1),
								action=el.action,
								next_state=torch.cat( [el.next_state, init_goal], dim=1),
								reward=reward,
								done=el.done
							)
				
				init_priority = memory.priority( torch.abs(init_el.reward).numpy() )
				
				memory.add(init_el,init_priority)

				#store for multiple goals :
				#1: sample new goal :
				goals = []
				for j in range(k) :
					goal = None
					if strategy == 'final' :
						goal = sample_goal(episode_buffer, strategy=strategy)
					elif strategy == 'future' :
						goal = sample_goal(episode_buffer, strategy=i)	
					goals.append(goal)
					

				#For each goal ...
				for goal in goals :
					#2: .. compute reward :
					goalreward = reward_function(el.state,goal)+el.reward
					#3: ... store this transistion with goal :
					goalel = EXP( state=torch.cat( [el.state, goal], dim=1),
									action=el.action,
									next_state = torch.cat( [el.next_state, goal], dim=1),
									reward = goalreward,
									done=el.done
								)
					
					init_priority = memory.priority( torch.abs(goalel.reward).numpy() )
					memory.add(goalel,init_priority)
					
				del el
				del goals
			

			del episode_buffer

		bashlogger.info('Complete')
		if path is not None :
			savemodel(model,path+'.save')
			bashlogger.info('Model saved : {}'.format(path) )
		
		env.close()
	except Exception as e :
		bashlogger.exception(e)
		

def savemodel(model,path='./modelRL.save') :
	torch.save( model.state_dict(), path)

def loadmodel(model,path='./modelRL.save') :
	model.load_state_dict( torch.load(path) )


def main():
	global nbr_actions
	#env = 'SpaceInvaders-v0'#gym.make('SpaceInvaders-v0')#.unwrapped
	#nbr_actions = 6
	env = 'Breakout-v0'#gym.make('Breakout-v0')#.unwrapped
	nbr_actions = 4
	
	resize = T.Compose([T.ToPILImage(),
					#T.Scale(84, interpolation=Image.CUBIC),
					#T.Scale(50, interpolation=Image.CUBIC),
					T.Scale(30, interpolation=Image.CUBIC),
					T.ToTensor() ] )

	last_sync = 0
	
	numep = 2000
	global BATCH_SIZE
	BATCH_SIZE = 4
	global GAMMA
	GAMMA = 0.999
	global MIN_MEMORY
	MIN_MEMORY = 5e3
	EPS_START = 0.5
	EPS_END = 0.05
	EPS_DECAY = 200
	alphaPER = 0.5
	global lr
	lr = 6.25e-5
	memoryCapacity = 6e3
	num_worker = 1
	#HER :
	k = 2
	strategy = 'future'

	model_path = './'+env+'::rescaleCNN+DoubleDQN+PR+HER-alpha'+str(alphaPER)+'-k'+str(k)+strategy+'-w'+str(num_worker)+'-lr'+str(lr)+'-b'+str(BATCH_SIZE)+'-m'+str(memoryCapacity)+'/'
	
	#mkdir :
	if not os.path.exists(model_path) :
		os.mkdir(model_path)
	path=model_path+env
	frompath = None

	savings =  [ p for p in os.listdir(model_path) if ('save' in p)==True ]
	if len(savings) :
		frompath = os.path.join(model_path,savings[0])


	#model = DQN(nbr_actions)
	model = DQN_HER(nbr_actions)
	#model = DuelingDQN(nbr_actions)
	model.share_memory()
	bashlogger.info('Model : created.')
	if frompath is not None :
		loadmodel(model,frompath)
		bashlogger.info('Model loaded: {}'.format(frompath))

	if use_cuda :
		bashlogger.info('Model : CUDA....')
		model = model.cuda()
		bashlogger.info('Model : CUDA : ok.')

	memory = PrioritizedReplayBuffer(capacity=memoryCapacity,alpha=alphaPER)
	bashlogger.info('Memory : ok.')

	workers = []
	for i in range(num_worker) :
		worker = Worker(i,model,env,memory,lr=lr,preprocess=resize,path=path,frompath=frompath,num_episodes=numep,epsend=EPS_END,epsstart=EPS_START,epsdecay=EPS_DECAY,k=k,strategy=strategy)
		workers.append(worker)
		time.sleep(1)
		worker.start()

	for i in range(num_worker) :
		try :
			workers[i].join()
		except Exception as e :
			bashlogger.info(e)

if __name__ == "__main__":
	main()


