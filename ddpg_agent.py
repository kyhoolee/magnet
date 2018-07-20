import datetime

import numpy as np
import tensorflow as tf
import argparse
import pommerman
from pommerman import agents
from utils import *
from model import *
from shaping import *
from actor_critic_nn import *
from pommerman import agents
from GreedyPolicy import GreedyPolicy
from ReplayBuffer import ReplayBuffer
from env_wrapper import EnvWrapper
import itertools

# Base learning rate for the Actor network
ACTOR_LEARNING_RATE = 0.0001
# Base learning rate for the Critic Network
CRITIC_LEARNING_RATE =  0.001
# Soft target update param
TAU = 0.001
MAX_EPISODES = 100000
MAX_STEPS_EPISODE = 50000
WARMUP_STEPS = 10000
EXPLORATION_EPISODES = 10000
GAMMA = 0.99
BUFFER_SIZE = 1000000
OU_THETA = 0.15
OU_MU = 0.
OU_SIGMA = 0.3
MIN_EPSILON = 0.1
MAX_EPSILON = 1
EVAL_PERIODS = 100
EVAL_EPISODES = 10
MINI_BATCH = 64
RANDOM_SEED = 123
ACTION_DIM = 1
STATE_DIM = 38*11
EXPLORE = 70
DATETIME = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
SUMMARY_DIR = './results/{}/tf_ddpg'.format(DATETIME)



OUTPUT_DIR = "./output"
class DdpgAgent(agents.BaseAgent):
    """The Random Agent that returns random actions given an action_space."""

    def __restart__(self, gamma=0.99):
        self.curr_state = None
        self.prev_state = None
        self.graph = np.random.rand(4, 120).astype("float32") + 0.0001
        self.pr_action = None
        self.gamma = gamma


    def __init__(self, id, sess, env = None, exploration_episodes=1000, max_episodes=300, max_steps_episode=300, warmup_steps=5000,\
            mini_batch=32, eval_episodes=10, eval_periods=100, env_render=False, summary_dir=SUMMARY_DIR, gamma=0.99, *args, **kwargs):

        super(DdpgAgent, self).__init__(*args, **kwargs)
        # Create the Estimator
        self.estimator_nn1 = tf.estimator.Estimator(model_fn=model_NN1, model_dir=OUTPUT_DIR + '/sa_nn1')
        # Set up logging for predictions
        self.tensors_to_logNN1 = {"probabilities": "softmax_tensor"}
        self.logging_hook_nn1 = tf.train.LoggingTensorHook(tensors=self.tensors_to_logNN1, every_n_iter=50)

        # Create the Estimator
        self.estimator_nn2 = tf.estimator.Estimator(model_fn=model_NN2, model_dir=OUTPUT_DIR + '/sa_nn2')
        # Set up logging for predictions
        self.tensors_to_logNN2 = {"probabilities": "softmax_tensor"}
        self.vlogging_hook_nn2 = tf.train.LoggingTensorHook(tensors=self.tensors_to_logNN2, every_n_iter=50)

        self.agent_num = id
        self.__restart__()
        #######init DDPG NN #####

        self.sess = sess
        self.env = env
        self.exploration_episodes = exploration_episodes
        self.max_episodes = max_episodes
        self.max_steps_episode = max_steps_episode
        self.warmup_steps = warmup_steps
        self.mini_batch = mini_batch
        self.eval_episodes = eval_episodes
        self.eval_periods = eval_periods
        self.env_render = env_render
        self.summary_dir = summary_dir

        self.writer = tf.summary.FileWriter(self.summary_dir, sess.graph)

        self.actor = ActorNetwork(sess, STATE_DIM, ACTION_DIM, ACTOR_LEARNING_RATE, TAU)

        cell = tf.contrib.rnn.BasicLSTMCell(num_units=300, state_is_tuple=True, reuse=None)
        cell_target = tf.contrib.rnn.BasicLSTMCell(num_units=300, state_is_tuple=True, reuse=None)
        self.critic = CriticNetwork(sess, STATE_DIM, ACTION_DIM, CRITIC_LEARNING_RATE, TAU, cell, cell_target, self.actor.get_num_trainable_vars())

        self.replay_buffer = ReplayBuffer(BUFFER_SIZE, RANDOM_SEED)
        self.noise = GreedyPolicy(ACTION_DIM, EXPLORATION_EPISODES, MIN_EPSILON, MAX_EPSILON)

    def act(self, obs, action_space):
        action = action_space.sample()

        self.prev_state = self.curr_state
        if self.pr_action is not None:
            self.curr_state = state_to_matrix_with_action(obs, action=self.pr_action).astype("float32")

        if self.prev_state is not None:
            curr_state_matrix = self.curr_state
            prev_state_matrix = self.prev_state

            pred_input_NN1 = tf.estimator.inputs.numpy_input_fn(
                x={"state1": prev_state_matrix,
                   "state2": curr_state_matrix,
                   "y": np.asmatrix(self.graph.flatten())},
                y=np.asmatrix(self.graph.flatten()),
                batch_size=1,
                num_epochs=None,
                shuffle=False)


            # Predict the estimator
            y_generator = self.estimator_nn1.predict(input_fn=pred_input_NN1)
            graph_predictions =  np.asmatrix(list(itertools.islice(y_generator, prev_state_matrix.shape[0]))[0]['graph'])
            input_to_ddpg = np.concatenate([self.curr_state, graph_predictions], axis=1)
            print(input_to_ddpg.shape)
            #action = self.actor.predict(np.expand_dims(input_to_ddpg, 0))[0, 0]

        self.pr_action = action

        return action

    def train_transformer(self, sess, env):
        self.sess = sess
        self.env = EnvWrapper(env, num_agent=self.agent_num)

        # Initialize target network weights

        for cur_episode in range(self.max_episodes):
            self.__restart__()
            # evaluate here.

            state = env.reset()
            episode_reward = 0

            for cur_step in range(self.max_steps_episode):

                if self.env_render:
                    self.env.render()

                action = self.env.action_space.sample()
                print(action, 'action')

                # 2. take action, see next state and reward :
                next_state, reward, terminal, info = self.env.step(action)

                graph_changed_manually, reward = reward_shaping(self.graph, next_state, state, self.agent_num)
                # 3. Save in replay buffer:
                self.replay_buffer.add(state, action, reward, graph_changed_manually.flatten(), next_state)

                # Keep adding experience to the memory until there are at least minibatch size samples
                if self.replay_buffer.size() > self.warmup_steps:
                    state_batch, action_batch, reward_batch, graph_changed_manually_batch, next_state_batch = \
                        self.replay_buffer.sample_batch(self.mini_batch)

                    # Calculate targets
                    train_input_NN1 = tf.estimator.inputs.numpy_input_fn(
                        x={"state1": state_batch,
                           "state2": next_state_batch},
                        y=np.asmatrix(graph_changed_manually_batch),
                        batch_size=1,
                        num_epochs=None,
                        shuffle=True)
                    print('train_input_NN1 data loaded')

                    # Train the estimator
                    self.estimator_nn1.train(input_fn=train_input_NN1, steps=1)

                state = next_state
                episode_reward += reward

                if terminal or cur_step == self.max_steps_episode - 1:
                    train_episode_summary = tf.Summary()
                    train_episode_summary.value.add(simple_value=episode_reward, tag="train/episode_reward")

                    self.writer.add_summary(train_episode_summary, cur_episode)
                    self.writer.flush()

                    print('Reward: %.2i' % int(episode_reward), ' | Episode', cur_episode)

                    break

    def train_ddpg(self, sess, env, epsilon=1.0, min_epsilon=0.01):
        self.sess = sess
        self.env = EnvWrapper(env, num_agent=self.agent_num)

        # Initialize target network weights

        train_writer = tf.summary.FileWriter(logdir='./logs', graph=tf.get_default_graph())
        t_summary = sess.run(tf.global_variables_initializer())

        for cur_episode in range(self.max_episodes):
            self.__restart__()
            # evaluate here.

            state = env.reset()
            episode_reward = 0
            episode_ave_max_q = 0
            max_state_episode = -1
            epsilon -= (epsilon / EXPLORE)
            if epsilon < min_epsilon:
                epsilon = min_epsilon

            for cur_step in range(self.max_steps_episode):

                if self.env_render:
                    self.env.render()

                # Add exploratory noise according to Ornstein-Uhlenbeck process to action
                if self.replay_buffer.size() < self.warmup_steps:
                    action = self.env.action_space.sample()
                    print(action, 'action')
                else:
                    action = self.noise.generate(self.actor.predict(np.expand_dims(state, 0))[0, 0], cur_episode)
                    print(action, 'action')

                # 2. take action, see next state and reward :
                next_state, reward, terminal, info = self.env.step(action)
                # 3. Save in replay buffer:
                self.replay_buffer.add(state, action, reward, terminal, next_state)

                # Keep adding experience to the memory until there are at least minibatch size samples
                if self.replay_buffer.size() > self.warmup_steps:
                    state_batch, action_batch, reward_batch, terminal_batch, next_state_batch = \
                        self.replay_buffer.sample_batch(self.mini_batch)

                    # Calculate targets

                    # 5. Train critic Network (states,actions, R + gamma* V(s', a')):
                    # 5.1 Get critic prediction = V(s', a')
                    # the a' is obtained using the actor prediction! or in other words : a' = actor(s')
                    target_q = self.critic.predict_target(next_state_batch, self.actor.predict_target(next_state_batch))
                    # 5.2 get y_t where:
                    y_i = np.reshape(reward_batch, (self.mini_batch, 1)) + (1 - np.reshape(terminal_batch,
                                                                                           (self.mini_batch, 1)).astype(
                                float)) \
                          * self.gamma * np.reshape(target_q, (self.mini_batch, 1))

                    # Update the critic given the targets
                    action_batch = np.reshape(action_batch, [self.mini_batch, 1])
                    predicted_q_value, _ = self.critic.train(state_batch, action_batch, np.reshape(y_i, (self.mini_batch, 1)), 20)
                    episode_ave_max_q += np.amax(predicted_q_value)

                    # Update the actor policy using the sampled gradient
                    a_outs = self.actor.predict(state_batch)
                    a_grads = self.critic.action_gradients(state_batch, a_outs)
                    self.actor.train(state_batch, a_grads[0])

                    # Update target networks
                    self.actor.update_target_network()
                    self.critic.update_target_network()

                state = next_state
                episode_reward += reward

                if terminal or cur_step == self.max_steps_episode - 1:
                    train_episode_summary = tf.Summary()
                    train_episode_summary.value.add(simple_value=episode_reward, tag="train/episode_reward")
                    train_episode_summary.value.add(simple_value=episode_ave_max_q / float(cur_step),
                                                    tag="train/episode_ave_max_q")
                    self.writer.add_summary(train_episode_summary, cur_episode)
                    self.writer.flush()

                    print('Reward: %.2i' % int(episode_reward), ' | Episode', cur_episode, \
                          '| Qmax: %.4f' % (episode_ave_max_q / float(cur_step)))

                    break