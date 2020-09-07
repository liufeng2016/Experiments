"""
experiment 3
"""

import tensorflow as tf
import numpy as np
import gym
import os
import shutil
import matplotlib.pyplot as plt
np.random.seed(1)
tf.set_random_seed(1)
TAU = 0.01
MAX_EPISODES = 2000
LR_A = 0.0005  # learning rate for actor
LR_C = 0.0005  # learning rate for critic
GAMMA = 0.999  # reward discount
REPLACE_ITER_A = 1700
REPLACE_ITER_C = 1500
MEMORY_CAPACITY = 200000
BATCH_SIZE = 32
DISPLAY_THRESHOLD = 100  # display until the running reward > 100
DATA_PATH = './data-y'
LOAD_MODEL = False
SAVE_MODEL_ITER = 100000
RENDER = True
OUTPUT_GRAPH = False
ENV_NAME = 'BipedalWalker-v2'
tf.reset_default_graph()
GLOBAL_STEP = tf.Variable(0, trainable=False)
INCREASE_GS = GLOBAL_STEP.assign(tf.add(GLOBAL_STEP, 1))
LR_A = tf.train.exponential_decay(LR_A, GLOBAL_STEP, 10000, .97, staircase=True)
LR_C = tf.train.exponential_decay(LR_C, GLOBAL_STEP, 10000, .97, staircase=True)
END_POINT = (200 - 10) * (14/30)    # from game

env = gym.make(ENV_NAME)
env.seed(1)

STATE_DIM = env.observation_space.shape[0]  # 24
ACTION_DIM = env.action_space.shape[0]  # 4
ACTION_BOUND = env.action_space.high    # [1, 1, 1, 1]

# all placeholder for tf
with tf.name_scope('S'):
    S = tf.placeholder(tf.float32, shape=[None, STATE_DIM], name='s')
with tf.name_scope('R'):
    R = tf.placeholder(tf.float32, [None, 1], name='r')
with tf.name_scope('S_'):
    S_ = tf.placeholder(tf.float32, shape=[None, STATE_DIM], name='s_')

###############################  Actor  ####################################

class Actor(object):
    def __init__(self, sess, action_dim, action_bound, learning_rate, t_replace_iter):
        self.sess = sess
        self.a_dim = action_dim
        self.action_bound = action_bound
        self.lr = learning_rate
        self.t_replace_iter = t_replace_iter
        self.t_replace_counter = 0

        with tf.variable_scope('Actor'):
            # input s, output a
            self.a = self._build_net(S, scope='eval_net', trainable=True)

            # input s_, output a, get a_ for critic
            self.a_ = self._build_net(S_, scope='target_net', trainable=False)
            
            self.t_a = self._build_net(S, scope='t_eval_net', trainable=False)
            self.t_a_ = self._build_net(S_, scope='t_target_net', trainable=False)

        self.e_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Actor/eval_net')
        self.t_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Actor/target_net')
        self.t_e_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Actor/t_eval_net')
        self.t_t_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Actor/t_target_net')


    def _build_net(self, s, scope, trainable):
        with tf.variable_scope(scope):
            init_w = tf.random_normal_initializer(0., 0.01)
            init_b = tf.constant_initializer(0.01)
            net = tf.layers.dense(s, 500, activation=tf.nn.relu,
                                  kernel_initializer=init_w, bias_initializer=init_b, name='l1', trainable=trainable)
            net = tf.layers.dense(net, 200, activation=tf.nn.relu,
                                  kernel_initializer=init_w, bias_initializer=init_b, name='l2', trainable=trainable)

            with tf.variable_scope('a'):
                actions = tf.layers.dense(net, self.a_dim, activation=tf.nn.tanh, kernel_initializer=init_w,
                                          bias_initializer=init_b, name='a', trainable=trainable)
                scaled_a = tf.multiply(actions, self.action_bound, name='scaled_a')  # Scale output to -action_bound to action_bound
        return scaled_a

    def learn(self, s):  # batch update
        self.sess.run(self.train_op, feed_dict={S: s})
        if self.t_replace_counter % self.t_replace_iter == 0:
            self.sess.run([tf.assign(t, e) for t, e in zip(self.t_params, self.e_params)])
        self.t_replace_counter += 1

    def choose_action(self, s):
        s = s[np.newaxis, :]    # single state
        return self.sess.run(self.a, feed_dict={S: s})[0]  # single action

    def add_grad_to_graph(self, a_grads):
        with tf.variable_scope('policy_grads'):
            # ys = policy;
            # xs = policy's parameters;
            # self.a_grads = the gradients of the policy to get more Q
            # tf.gradients will calculate dys/dxs with a initial gradients for ys, so this is dq/da * da/dparams
            self.policy_grads_and_vars = tf.gradients(ys=self.a, xs=self.e_params, grad_ys=a_grads)

        with tf.variable_scope('A_train'):
            opt = tf.train.RMSPropOptimizer(-self.lr)  # (- learning rate) for ascent policy
            self.train_op = opt.apply_gradients(zip(self.policy_grads_and_vars, self.e_params), global_step=GLOBAL_STEP)
    def update_target(self):
        self.sess.run([tf.assign(t, e) for t, e in zip(self.t_e_params, self.e_params)])
        self.sess.run([tf.assign(t, e) for t, e in zip(self.t_t_params, self.t_params)])

    def soft_go_back(self):
        e_list=tf.global_variables(scope = 'Actor/eval_net')
        t_list=tf.global_variables(scope = 'Actor/target_net')
        e_ini = tf.variables_initializer(e_list)
        t_ini = tf.variables_initializer(t_list)
        self.sess.run(e_ini)
        self.sess.run(t_ini)
        self.sess.run([[tf.assign(t, tt*(1-TAU)+t*TAU) ,tf.assign(e, te*(1-TAU)+e*TAU)]for tt,te, t, e in zip(self.t_t_params, self.t_e_params,self.t_params, self.e_params)])

###############################  Critic  ####################################

class Critic(object):
    def __init__(self, sess, state_dim, action_dim, learning_rate, gamma, t_replace_iter, a, a_):
        self.sess = sess
        self.s_dim = state_dim
        self.a_dim = action_dim
        self.lr = learning_rate
        self.gamma = gamma
        self.t_replace_iter = t_replace_iter
        self.t_replace_counter = 0

        with tf.variable_scope('Critic'):
            # Input (s, a), output q
            self.a = a
            self.q = self._build_net(S, self.a, 'eval_net', trainable=True)

            # Input (s_, a_), output q_ for q_target
            self.q_ = self._build_net(S_, a_, 'target_net', trainable=False)    # target_q is based on a_ from Actor's target_net

            self.e_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Critic/eval_net')
            self.t_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Critic/target_net')

            self.t_q = self._build_net(S, self.a, 't_eval_net', trainable=False)
            self.t_q_ = self._build_net(S_, a_, 't_target_net', trainable=False)    # target_q is based on a_ from Actor's target_net

            self.t_e_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Critic/t_eval_net')
            self.t_t_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Critic/t_target_net')

       
        with tf.variable_scope('target_q'):
            self.target_q = R + self.gamma * self.q_

        with tf.variable_scope('abs_TD'):
            self.abs_td = tf.abs(self.target_q - self.q)
        self.ISWeights = tf.placeholder(tf.float32, [None, 1], name='IS_weights')
        with tf.variable_scope('TD_error'):
            self.loss = tf.reduce_mean(self.ISWeights * tf.squared_difference(self.target_q, self.q))

        with tf.variable_scope('C_train'):
            self.train_op = tf.train.AdamOptimizer(self.lr).minimize(self.loss, global_step=GLOBAL_STEP)

        with tf.variable_scope('a_grad'):
            self.a_grads = tf.gradients(self.q, a)[0]   # tensor of gradients of each sample (None, a_dim)

    def _build_net(self, s, a, scope, trainable):
        with tf.variable_scope(scope):
            init_w = tf.random_normal_initializer(0., 0.01)
            init_b = tf.constant_initializer(0.01)

            with tf.variable_scope('l1'):
                n_l1 = 700
                # combine the action and states together in this way
                w1_s = tf.get_variable('w1_s', [self.s_dim, n_l1], initializer=init_w, trainable=trainable)
                w1_a = tf.get_variable('w1_a', [self.a_dim, n_l1], initializer=init_w, trainable=trainable)
                b1 = tf.get_variable('b1', [1, n_l1], initializer=init_b, trainable=trainable)
                net = tf.nn.relu(tf.matmul(s, w1_s) + tf.matmul(a, w1_a) + b1)
            with tf.variable_scope('l2'):
                net = tf.layers.dense(net, 20, activation=tf.nn.relu, kernel_initializer=init_w,
                                      bias_initializer=init_b, name='l2', trainable=trainable)
            with tf.variable_scope('q'):
                q = tf.layers.dense(net, 1, kernel_initializer=init_w, bias_initializer=init_b, trainable=trainable)   # Q(s,a)
        return q

    def learn(self, s, a, r, s_, ISW):
        _, abs_td = self.sess.run([self.train_op, self.abs_td], feed_dict={S: s, self.a: a, R: r, S_: s_, self.ISWeights: ISW})
        if self.t_replace_counter % self.t_replace_iter == 0:
            self.sess.run([tf.assign(t, e) for t, e in zip(self.t_params, self.e_params)])
        self.t_replace_counter += 1
        return abs_td

    def update_target(self):
        self.sess.run([tf.assign(t, e) for t, e in zip(self.t_e_params, self.e_params)])
        self.sess.run([tf.assign(t, e) for t, e in zip(self.t_t_params, self.t_params)])
        
    def soft_go_back(self):
        e_list=tf.global_variables(scope = 'Critic/eval_net')
        t_list=tf.global_variables(scope = 'Critic/target_net')
        e_ini = tf.variables_initializer(e_list)
        t_ini = tf.variables_initializer(t_list)
        self.sess.run(e_ini)
        self.sess.run(t_ini)
        self.sess.run([[tf.assign(t, tt*(1-TAU)+t*TAU) ,tf.assign(e, te*(1-TAU)+e*TAU)]for tt,te, t, e in zip(self.t_t_params, self.t_e_params,self.t_params, self.e_params)])
        
        
class SumTree(object):
    """
    This SumTree code is modified version and the original code is from:
    https://github.com/jaara/AI-blog/blob/master/SumTree.py

    Story the data with it priority in tree and data frameworks.
    """
    data_pointer = 0

    def __init__(self, capacity):
        self.capacity = capacity  # for all priority values
        self.tree = np.zeros(2 * capacity - 1)+1e-5
        # [--------------Parent nodes-------------][-------leaves to recode priority-------]
        #             size: capacity - 1                       size: capacity
        self.data = np.zeros(capacity, dtype=object)  # for all transitions
        # [--------------data frame-------------]
        #             size: capacity

    def add_new_priority(self, p, data):
        leaf_idx = self.data_pointer + self.capacity - 1

        self.data[self.data_pointer] = data  # update data_frame
        self.update(leaf_idx, p)  # update tree_frame
        self.data_pointer += 1
        if self.data_pointer >= self.capacity:  # replace when exceed the capacity
            self.data_pointer = 0

    def update(self, tree_idx, p):
        change = p - self.tree[tree_idx]

        self.tree[tree_idx] = p
        self._propagate_change(tree_idx, change)

    def _propagate_change(self, tree_idx, change):
        """change the sum of priority value in all parent nodes"""
        parent_idx = (tree_idx - 1) // 2
        self.tree[parent_idx] += change
        if parent_idx != 0:
            self._propagate_change(parent_idx, change)

    def get_leaf(self, lower_bound):
        leaf_idx = self._retrieve(lower_bound)  # search the max leaf priority based on the lower_bound
        data_idx = leaf_idx - self.capacity + 1
        return [leaf_idx, self.tree[leaf_idx], self.data[data_idx]]

    def _retrieve(self, lower_bound, parent_idx=0):
        """
        Tree structure and array storage:

        Tree index:
             0         -> storing priority sum
            / \
          1     2
         / \   / \
        3   4 5   6    -> storing priority for transitions

        Array type for storing:
        [0,1,2,3,4,5,6]
        """
        left_child_idx = 2 * parent_idx + 1
        right_child_idx = left_child_idx + 1

        if left_child_idx >= len(self.tree):  # end search when no more child
            return parent_idx

        if self.tree[left_child_idx] == self.tree[right_child_idx]:
            return self._retrieve(lower_bound, np.random.choice([left_child_idx, right_child_idx]))
        if lower_bound <= self.tree[left_child_idx]:  # downward search, always search for a higher priority node
            return self._retrieve(lower_bound, left_child_idx)
        else:
            return self._retrieve(lower_bound - self.tree[left_child_idx], right_child_idx)

    @property
    def root_priority(self):
        return self.tree[0]  # the root


class Memory(object):  # stored as ( s, a, r, s_ ) in SumTree
    """
    This SumTree code is modified version and the original code is from:
    https://github.com/jaara/AI-blog/blob/master/Seaquest-DDQN-PER.py
    """
    epsilon = 0.001  # small amount to avoid zero priority
    alpha = 0.6  # [0~1] convert the importance of TD error to priority
    beta = 0.4  # importance-sampling, from initial value increasing to 1
    beta_increment_per_sampling = 1e-5  # annealing the bias
    abs_err_upper = 1   # for stability refer to paper

    def __init__(self, capacity):
        self.tree = SumTree(capacity)

    def store(self, error, transition):
        p = self._get_priority(error)
        self.tree.add_new_priority(p, transition)

    def prio_sample(self, n):
        batch_idx, batch_memory, ISWeights = [], [], []
        segment = self.tree.root_priority / n
        self.beta = np.min([1, self.beta + self.beta_increment_per_sampling])  # max = 1

        min_prob = np.min(self.tree.tree[-self.tree.capacity:]) / self.tree.root_priority
        maxiwi = np.power(self.tree.capacity * min_prob, -self.beta)  # for later normalizing ISWeights
        for i in range(n):
            a = segment * i
            b = segment * (i + 1)
            lower_bound = np.random.uniform(a, b)
            while True:
                idx, p, data = self.tree.get_leaf(lower_bound)
                if type(data) is int:
                    i -= 1
                    lower_bound = np.random.uniform(segment * i, segment * (i+1))
                else:
                    break
            prob = p / self.tree.root_priority
            ISWeights.append(self.tree.capacity * prob)
            batch_idx.append(idx)
            batch_memory.append(data)

        ISWeights = np.vstack(ISWeights)
        ISWeights = np.power(ISWeights, -self.beta) / maxiwi  # normalize
        return batch_idx, np.vstack(batch_memory), ISWeights

    def random_sample(self, n):
        idx = np.random.randint(0, self.tree.capacity, size=n, dtype=np.int)
        return np.vstack(self.tree.data[idx])

    def update(self, idx, error):
        p = self._get_priority(error)
        self.tree.update(idx, p)

    def _get_priority(self, error):
        error += self.epsilon   # avoid 0
        clipped_error = np.clip(error, 0, self.abs_err_upper)
        return np.power(clipped_error, self.alpha)


sess = tf.Session()

# Create actor and critic.
actor = Actor(sess, ACTION_DIM, ACTION_BOUND, LR_A, REPLACE_ITER_A)
critic = Critic(sess, STATE_DIM, ACTION_DIM, LR_C, GAMMA, REPLACE_ITER_C, actor.a, actor.a_)
actor.add_grad_to_graph(critic.a_grads)

M = Memory(MEMORY_CAPACITY)

saver = tf.train.Saver(max_to_keep=100)

if LOAD_MODEL:
    all_ckpt = tf.train.get_checkpoint_state(DATA_PATH, 'checkpoint').all_model_checkpoint_paths
    saver.restore(sess, all_ckpt[-1])
    while True:
        s = env.reset()
        while True:
            env.render()
            s, r, done, _ = env.step(actor.choose_action(s))
            if done:
                break 
else:
    if os.path.isdir(DATA_PATH): shutil.rmtree(DATA_PATH)
    os.mkdir(DATA_PATH)
    sess.run(tf.global_variables_initializer())

if OUTPUT_GRAPH:
    tf.summary.FileWriter('logs', graph=sess.graph)
    
    
#############################################################
def check_h_s(n_r):
    ner =0
    for e in er_bath:
        ner += e
    ner = ner/len(er_bath)
    if ner>er_max : #and ner<n_r:
        return True , ner
    else:
        return False , ner

def chec_restore(ep,n_er,actor,critic):
    global low_count,A_LR,C_LR,ReLoad,TAU
    if n_er < er_max:
        low_count+=1
    else:
        low_count = 0
    if low_count > RESTORE_COUNT and MEM_EN:
        low_count = 0
        actor.soft_go_back()
        critic.soft_go_back()
        print('restore*****')
        # seed=np.random.randint(0,9)
        # env.seed(seed)
        ReLoad += 1
        if len(reload_index)==0:
            reload_index.append(ep)   
        else:
            if(ep - reload_index[-1])>150:
                reload_index.append(ep)



var = 3  # control exploration
var_min = 0.01
all_ep_r = []
er_bath = []
er_bath_num = 50
er_h_list = []
er_h_list.append([0,-100])
er_max = -100
er_max_list=[]
reload_index=[]
low_count = 0
RESTORE_COUNT = 36
MEM_EN = False
ReLoad = 0
res = False

for ep in range(MAX_EPISODES):
    er_max_list.append(er_max)
    seed=np.random.randint(0,9)
    np.random.seed(seed)
    # tf.set_random_seed(seed)
    # env.seed(seed)
    # s = (hull angle speed, angular velocity, horizontal speed, vertical speed, position of joints and joints angular speed, legs contact with ground, and 10 lidar rangefinder measurements.)
    s = env.reset()
    ep_r = 0
    n_r = 0
    while True:
    # for i in range(2000):
        # if i_episode  % 50==0:
        #     env.render()
        a = actor.choose_action(s)
        a = np.clip(np.random.normal(a, var), -1, 1)    # add randomness to action selection for exploration
        s_, r, done, _ = env.step(a)    # r = total 300+ points up to the far end. If the robot falls, it gets -100.

        if r == -100: r = -2
        ep_r += r
        
        n_r = r
        transition = np.hstack((s, a, [r], s_))
        max_p = np.max(M.tree.tree[-M.tree.capacity:])
        M.store(max_p, transition)

        if GLOBAL_STEP.eval(sess) > MEMORY_CAPACITY/20:
            var = max([var*0.9999, var_min])  # decay the action randomness
            tree_idx, b_M, ISWeights = M.prio_sample(BATCH_SIZE)    # for critic update
            b_s = b_M[:, :STATE_DIM]
            b_a = b_M[:, STATE_DIM: STATE_DIM + ACTION_DIM]
            b_r = b_M[:, -STATE_DIM - 1: -STATE_DIM]
            b_s_ = b_M[:, -STATE_DIM:]

            abs_td = critic.learn(b_s, b_a, b_r, b_s_, ISWeights)
            actor.learn(b_s)
            for i in range(len(tree_idx)):  # update priority
                idx = tree_idx[i]
                M.update(idx, abs_td[i])
        if GLOBAL_STEP.eval(sess) % SAVE_MODEL_ITER == 0:
            ckpt_path = os.path.join(DATA_PATH, 'DDPG.ckpt')
            save_path = saver.save(sess, ckpt_path, global_step=GLOBAL_STEP, write_meta_graph=False)
            print("\nSave Model %s\n" % save_path)

        if done:
            if "running_r" not in globals():
                running_r = ep_r
            else:
                running_r = 0.95*running_r + 0.05*ep_r
            if running_r > DISPLAY_THRESHOLD: RENDER = True
            else: RENDER = True

            done = '| Achieve ' if env.unwrapped.hull.position[0] >= END_POINT else '| -----'
            print('Episode:', ep,
                  done,
                  '| Running_r: %i' % int(running_r),
                  '| Epi_r: %.2f' % ep_r,
                  '| Exploration: %.3f' % var,
                  '| Pos: %.i' % int(env.unwrapped.hull.position[0]),
                  '| LR_A: %.6f' % sess.run(LR_A),
                  '| LR_C: %.6f' % sess.run(LR_C),
                  )
            break

        s = s_
        sess.run(INCREASE_GS)
    
    if ep == 0: all_ep_r.append(ep_r)
    else: all_ep_r.append(all_ep_r[-1]*0.7 + ep_r*0.3) 
    
    er_bath.append(ep_r)
    if len(er_bath)>er_bath_num:
        er_bath.remove(er_bath[0])
        res , n_er = check_h_s(n_r)
        if ep>1000:
            chec_restore(ep,n_er,actor,critic)  
        if res and (ep-er_h_list[-1][0]) > 2  :
            er_h_list.append([ep,n_er])
            er_max = n_er
            actor.update_target()
            critic.update_target()
            print('update target*****')
            MEM_EN = True
            # if er_max > -250:
                # DATA_PATH = './log_lr/' +str(ep)+'_'+str(int(er_max))
                # ppo.save(DATA_PATH)
                # test('yunxzhong')
                
plt.plot(np.arange(len(all_ep_r)), all_ep_r,color='#00E5EE',linewidth=1)
plt.plot(np.arange(len(er_max_list)), er_max_list,color='r',linewidth=1.5)
reload_y=[]
for x in reload_index:
    reload_y.append(all_ep_r[x])
plt.plot(reload_index, reload_y,'om')
plt.xlabel("Training Episodes");plt.ylabel('Averaged Reward');plt.show()
env.close()        
print('ReLoad=',ReLoad)       
        
        