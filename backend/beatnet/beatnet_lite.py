# BeatNetLite.py

import os
import numpy as np
import librosa
import time
import torch.nn as nn
import torch
import torch.nn.functional as F
import json
import itertools as it

try:
  from numba import njit
except ModuleNotFoundError:  # pragma: no cover
  def njit(func=None, **kwargs):  # dummy decorator
    if func is None:
      return lambda f: f
    return func

FREF = 440.0


@njit(cache=True)
def _frame_audio(x: np.ndarray,
                 frame_size: int,
                 hop: int) -> np.ndarray:
  """
  • First frame is centred on sample 0
  • Last frame is centred on sample len(x)-1
  • DC-free, so zero padding is correct
  """
  pad = frame_size // 2
  n_frames = (len(x) - 1) // hop + 1

  out = np.zeros((n_frames, frame_size), dtype=np.float32)

  for t in range(n_frames):
    centre = t * hop
    left = centre - pad  # index in original signal
    for j in range(frame_size):
      idx = left + j
      if 0 <= idx < len(x):
        out[t, j] = x[idx]  # real sample
      # else retain the 0.0 written during initialisation
  return out


def _build_log_tri_fb(sr: int,
                      n_fft: int,
                      bands_per_oct: int = 24,
                      fmin: float = 30.0,
                      fmax: float = 17_000.0,
                      norm_filters: bool = True) -> np.ndarray:
  """
  Logarithmic triangular filter-bank
  Returns
  -------
  fb : np.ndarray, shape (n_fft//2 + 1, n_bands), dtype float32
  """
  # 1. logarithmic centre frequencies
  left1 = np.floor(np.log2(float(fmin) / FREF) * bands_per_oct)
  right1 = np.ceil(np.log2(float(fmax) / FREF) * bands_per_oct)
  freqs = FREF * 2.0 ** (np.arange(left1, right1) / float(bands_per_oct))
  freqs = freqs[np.searchsorted(freqs, fmin):]
  freqs = freqs[:np.searchsorted(freqs, fmax, side='right')]
  centres_hz = freqs

  # 2. map to FFT bins (madmom tie-break & unique bins)
  bin_freqs = np.linspace(0.0, sr / 2.0, n_fft // 2 + 1, dtype=float)[:-1]
  # closest bins, tie-break toward the lower bin
  indices = bin_freqs.searchsorted(centres_hz)
  indices = np.clip(indices, 1, len(bin_freqs) - 1)
  left = bin_freqs[indices - 1]
  right = bin_freqs[indices]
  indices -= centres_hz - left < right - centres_hz
  indices = np.unique(indices)
  centres_bin = indices

  centres_bin = centres_bin[centres_bin >= 1]

  # 3. build one triangle per sliding triple of bins
  n_bins = bin_freqs.size
  triangles = []
  for start, centre, stop in zip(centres_bin[:-2],
                                 centres_bin[1:-1],
                                 centres_bin[2:]):
    if stop - start < 2:
      centre = start  # move peak left
      stop = start + 1  # width-1 filter

    wl, wr = centre - start, stop - centre
    # allow width-1 triangles (madmom keeps them and renormalises)
    data = np.zeros(n_bins, dtype=np.float32)
    if wl:  # rising edge
      data[start:centre] = np.linspace(0.0, 1.0, wl, endpoint=False)
    data[centre:stop] = np.linspace(1.0, 0.0, wr, endpoint=False)

    if norm_filters:
      s = data.sum()
      if s > 0.0:
        data /= s
    triangles.append(data)

  fb = np.stack(triangles, axis=1).astype(np.float32)  # (bins, bands)
  return fb


def compute_features(
    x: np.ndarray,
) -> np.ndarray:
  sr: int = 22_050
  win_length = int(0.064 * sr)
  hop_length = int(0.020 * sr)
  frames = _frame_audio(x, win_length, hop_length)
  window = np.hanning(win_length).astype(np.float32)
  win64 = window.astype(np.float64)
  frames64 = frames.astype(np.float64)
  fft_in = frames64 * win64
  spec_complex = np.fft.fft(fft_in, n=win_length, axis=1)
  stft_mag = np.abs(spec_complex[:, : win_length // 2]).astype(np.float32)
  fb = _build_log_tri_fb(
    sr,
    win_length,
    bands_per_oct=24,
    fmin=30.,
    fmax=17_000.,
    norm_filters=True,
  ).astype(np.float32)
  band_energy = stft_mag @ fb
  log_spec = np.log10(1.0 + band_energy)
  # first frame has no past context → set to 0
  delta = np.empty_like(log_spec, dtype=np.float32)
  delta[0] = 0.0
  # positive first-order difference (same rule madmom uses)
  delta[1:] = np.maximum(0.0, log_spec[1:] - log_spec[:-1])
  return np.hstack((log_spec, delta)).astype(np.float32)


#  Numba‑accelerated Viterbi inner loop
@njit(cache=True)
def _viterbi_numba(ptrs, idx, log_A, log_pi, log_B):
  T, K = log_B.shape

  very_neg = -1e100  # acts as -inf but is NumPy/Numba‑safe
  delta = np.empty((T, K), dtype=np.float64)
  psi = np.empty((T, K), dtype=np.int32)

  # initialise t = 0
  for k in range(K):
    delta[0, k] = log_pi[k] + log_B[0, k]
    psi[0, k] = -1

  # recursion
  for t in range(1, T):
    for s in range(K):
      start = ptrs[s]
      stop = ptrs[s + 1]
      if start == stop:  # no incoming transitions
        delta[t, s] = very_neg
        psi[t, s] = -1
        continue
      best_score = very_neg
      best_state = -1
      for i in range(start, stop):
        prev = idx[i]
        score = delta[t - 1, prev] + log_A[i]
        if score > best_score:
          best_score = score
          best_state = prev
      delta[t, s] = best_score + log_B[t, s]
      psi[t, s] = best_state

  # termination
  log_P = very_neg
  last = -1
  for s in range(K):
    if delta[T - 1, s] > log_P:
      log_P = delta[T - 1, s]
      last = s

  # back‑track
  path = np.empty(T, dtype=np.uint32)
  path[T - 1] = last
  for t in range(T - 2, -1, -1):
    path[t] = psi[t + 1, path[t + 1]]

  return path, log_P


# Hidden Markov Model that calls the Numba backend
class HiddenMarkovModel:
  """HMM that relies on a Numba‑JIT Viterbi implementation."""

  def __init__(self, transition_model,
               observation_model,
               initial_distribution=None):
    self.transition_model = transition_model
    self.observation_model = observation_model
    self.K = transition_model.num_states

    if initial_distribution is None:
      pi = np.full(self.K, 1.0 / self.K, dtype=float)
    else:
      pi = np.asarray(initial_distribution, dtype=float)
      if pi.size != self.K:
        raise ValueError("initial_distribution has wrong length")
      pi = pi / pi.sum()
    self.log_pi = np.log(pi)

  # Viterbi decoder
  def viterbi(self, observations):
    """Return the most likely state path and its log‑probability."""
    obs = np.ascontiguousarray(observations, dtype=float)
    log_B_full = self.observation_model.log_densities(obs)
    log_B = log_B_full[:, self.observation_model.pointers]

    path, log_p = _viterbi_numba(self.transition_model.pointers,
                                 self.transition_model.states,
                                 self.transition_model.log_probabilities,
                                 self.log_pi,
                                 log_B.astype(np.float64))
    return path, float(log_p)


# transition distributions
def exponential_transition(from_intervals, to_intervals, transition_lambda,
                           threshold=np.spacing(1), norm=True):
  # no transition lambda
  if transition_lambda is None:
    # return a diagonal matrix
    return np.diag(np.diag(np.ones((len(from_intervals),
                                    len(to_intervals)))))
  # compute the transition probabilities
  ratio = (to_intervals.astype(float) /
           from_intervals.astype(float)[:, np.newaxis])
  prob = np.exp(-transition_lambda * abs(ratio - 1.))
  # set values below threshold to 0
  prob[prob <= threshold] = 0
  # normalize the emission probabilities
  if norm:
    prob /= np.sum(prob, axis=1)[:, np.newaxis]
  return prob


class BeatStateSpace(object):
  def __init__(self, min_interval, max_interval, num_intervals=None):
    # per default, use a linear spacing of the tempi
    intervals = np.arange(np.round(min_interval),
                          np.round(max_interval) + 1)
    # if num_intervals is given (and smaller than the length of the linear
    # spacing of the intervals) use a log spacing and limit the number of
    # intervals to the given value
    if num_intervals is not None and num_intervals < len(intervals):
      # we must approach the number of intervals iteratively
      num_log_intervals = num_intervals
      intervals = []
      while len(intervals) < num_intervals:
        intervals = np.logspace(np.log2(min_interval),
                                np.log2(max_interval),
                                num_log_intervals, base=2)
        # quantize to integer intervals
        intervals = np.unique(np.round(intervals))
        num_log_intervals += 1
    # save the intervals
    self.intervals = np.ascontiguousarray(intervals, dtype=int)
    # number of states and intervals
    self.num_states = int(np.sum(intervals))
    self.num_intervals = len(intervals)
    # define first and last states
    first_states = np.cumsum(np.r_[0, self.intervals[:-1]])
    self.first_states = first_states.astype(int)
    self.last_states = np.cumsum(self.intervals) - 1
    # define the positions and intervals of the states
    self.state_positions = np.empty(self.num_states)
    self.state_intervals = np.empty(self.num_states, dtype=int)
    # Note: having an index counter is faster than ndenumerate
    idx = 0
    for i in self.intervals:
      self.state_positions[idx: idx + i] = np.linspace(0, 1, i,
                                                       endpoint=False)
      self.state_intervals[idx: idx + i] = i
      idx += i


class RNNDownBeatTrackingObservationModel(object):
  def __init__(self, state_space, observation_lambda):
    self.observation_lambda = observation_lambda
    # compute observation pointers
    # always point to the non-beat densities
    pointers = np.zeros(state_space.num_states, dtype=np.uint32)
    # unless they are in the beat range of the state space
    border = 1. / observation_lambda
    pointers[state_space.state_positions % 1 < border] = 1
    # the downbeat (i.e. the first beat range) points to density column 2
    pointers[state_space.state_positions < border] = 2
    # instantiate a ObservationModel with the pointers

    # super(RNNDownBeatTrackingObservationModel, self).__init__(pointers)
    if pointers.dtype != np.uint32:
      raise ValueError("pointers must be uint32")
    self.pointers = np.ascontiguousarray(pointers, dtype=np.uint32)

  def log_densities(self, observations):
    # init densities
    log_densities = np.empty((len(observations), 3), dtype=float)
    # Note: it's faster to call np.log multiple times instead of once on
    #       the whole 2d array
    log_densities[:, 0] = np.log((1. - np.sum(observations, axis=1)) /
                                 (self.observation_lambda - 1))
    log_densities[:, 1] = np.log(observations[:, 0])
    log_densities[:, 2] = np.log(observations[:, 1])
    # return the densities
    return log_densities

  def densities(self, observations: np.ndarray) -> np.ndarray:
    """Generic helper that exponentiates the log-densities."""
    return np.exp(self.log_densities(observations))


def open_file(filename, mode='r'):
  # check if we need to open the file
  if isinstance(filename, str):
    f = fid = open(filename, mode)
  else:
    f = filename
    fid = None
  # yield an open file handle
  yield f
  # close the file if needed
  if fid:
    fid.close()


def threshold_activations(activations, threshold):
  first = last = 0
  # use only the activations > threshold
  idx = np.nonzero(activations >= threshold)[0]
  if idx.any():
    first = max(first, np.min(idx))
    last = min(len(activations), np.max(idx) + 1)
  # return thresholded activations segment and first index
  return activations[first:last], first


class BarStateSpace(object):
  def __init__(self, num_beats, min_interval, max_interval,
               num_intervals=None):
    # model N beats as a bar
    self.num_beats = int(num_beats)
    self.state_positions = np.empty(0)
    self.state_intervals = np.empty(0, dtype=int)
    self.num_states = 0
    # save the first and last states of the individual beats in a list
    self.first_states = []
    self.last_states = []
    # create a BeatStateSpace and stack it `num_beats` times
    bss = BeatStateSpace(min_interval, max_interval, num_intervals)
    for b in range(self.num_beats):
      # define position (add beat counter) and interval states
      self.state_positions = np.hstack((self.state_positions,
                                        bss.state_positions + b))
      self.state_intervals = np.hstack((self.state_intervals,
                                        bss.state_intervals))
      # add the current number of states as offset
      self.first_states.append(bss.first_states + self.num_states)
      self.last_states.append(bss.last_states + self.num_states)
      # finally increase the number of states
      self.num_states += bss.num_states


class BarTransitionModel(object):
  def __init__(self, state_space, transition_lambda):
    # expand transition_lambda to a list if a single value is given
    if not isinstance(transition_lambda, list):
      transition_lambda = [transition_lambda] * state_space.num_beats
    if state_space.num_beats != len(transition_lambda):
      raise ValueError('length of `transition_lambda` must be equal to '
                       '`num_beats` of `state_space`.')
    # save attributes
    self.state_space = state_space
    self.transition_lambda = transition_lambda
    # same tempo transitions probabilities within the state space is 1
    # Note: use all states, but remove all first states of the individual
    #       beats, because there are no same tempo transitions into them
    states = np.arange(state_space.num_states, dtype=np.uint32)
    states = np.setdiff1d(states, state_space.first_states)
    prev_states = states - 1
    probabilities = np.ones_like(states, dtype=float)
    # tempo transitions occur at the boundary between beats (unless the
    # corresponding transition_lambda is set to None)
    for beat in range(state_space.num_beats):
      # connect to the first states of the actual beat
      to_states = state_space.first_states[beat]
      # connect from the last states of the previous beat
      from_states = state_space.last_states[beat - 1]
      # transition follow an exponential tempo distribution
      from_int = state_space.state_intervals[from_states]
      to_int = state_space.state_intervals[to_states]
      prob = exponential_transition(from_int, to_int,
                                    transition_lambda[beat])
      # use only the states with transitions to/from != 0
      from_prob, to_prob = np.nonzero(prob)
      states = np.hstack((states, to_states[to_prob]))
      prev_states = np.hstack((prev_states, from_states[from_prob]))
      probabilities = np.hstack((probabilities, prob[prob != 0]))
    # make the transitions sparse
    transitions = self.make_sparse(states, prev_states, probabilities)
    # instantiate a TransitionModel

    self.states = np.ascontiguousarray(transitions[0], dtype=np.uint32)
    self.pointers = np.ascontiguousarray(transitions[1], dtype=np.uint32)
    self.probabilities = np.ascontiguousarray(transitions[2], dtype=float)
    self.log_probabilities = np.log(self.probabilities)
    self.num_states = len(self.pointers) - 1

  @staticmethod
  def make_sparse(states, prev_states, probabilities, normalise=True):
    states = np.asarray(states, dtype=np.uint32)
    prev_states = np.asarray(prev_states, dtype=np.uint32)
    probabilities = np.asarray(probabilities, dtype=float)

    if not (len(states) == len(prev_states) == len(probabilities)):
      raise ValueError("states, prev_states and probabilities must share length")

    n_states = int(max(states.max(), prev_states.max())) + 1
    buckets = [[] for _ in range(n_states)]
    for dst, src, p in zip(states, prev_states, probabilities):
      buckets[dst].append((src, p))

    ptr = [0]
    idx = []
    probs = []
    for b in buckets:
      if b:
        srcs, pr = zip(*b)
        srcs = np.asarray(srcs, dtype=np.uint32)
        pr = np.asarray(pr, dtype=float)
        if normalise:
          s = pr.sum()
          if s == 0:
            raise ValueError("transition probabilities into a state sum to 0")
          pr /= s
        idx.append(srcs)
        probs.append(pr)
      ptr.append(ptr[-1] + (0 if not b else len(b)))

    states_out = np.concatenate(idx).astype(np.uint32) if idx else np.empty(0, dtype=np.uint32)
    probs_out = np.concatenate(probs) if probs else np.empty(0, dtype=float)
    return states_out, np.asarray(ptr, dtype=np.uint32), probs_out


def _process_dbn(process_tuple):
  return process_tuple[0].viterbi(process_tuple[1])


class BDA(nn.Module):  # beat_downbeat_activation
  def __init__(self, dim_in, num_cells, num_layers):
    super(BDA, self).__init__()

    self.dim_in = dim_in
    self.dim_hd = num_cells
    self.num_layers = num_layers
    self.conv_out = 150
    self.kernelsize = 10
    self.conv1 = nn.Conv1d(1, 2, self.kernelsize)
    self.linear0 = nn.Linear(2 * int((self.dim_in - self.kernelsize + 1) / 2),
                             self.conv_out)  # divide to 2 is for max pooling filter
    self.lstm = nn.LSTM(input_size=self.conv_out,  # self.dim_in
                        hidden_size=self.dim_hd,
                        num_layers=self.num_layers,
                        batch_first=True,
                        bidirectional=False,
                        )

    self.linear = nn.Linear(in_features=self.dim_hd,
                            out_features=3)

    self.softmax = nn.Softmax(dim=0)
    # Initialize the hidden state and cell state
    self.hidden = torch.zeros(2, 1, self.dim_hd).to('cpu')
    self.cell = torch.zeros(2, 1, self.dim_hd).to('cpu')
    self.to('cpu')

  def forward(self, data):
    x = data
    x = torch.reshape(x, (-1, self.dim_in))
    x = x.unsqueeze(0).transpose(0, 1)
    x = F.max_pool1d(F.relu(self.conv1(x)), 2)
    size = x.size()[1:]  # all dimensions except the batch dimension
    num_features = 1
    for s in size:
      num_features *= s
    x = x.view(-1, num_features)
    x = self.linear0(x)
    x = torch.reshape(x, (np.shape(data)[0], np.shape(data)[1], self.conv_out))
    x, (self.hidden, self.cell) = self.lstm(x, (self.hidden, self.cell))
    out = self.linear(x)
    out = out.transpose(1, 2)
    return out

  def final_pred(self, inp):
    return self.softmax(inp)

class BeatNetLite:
  def __init__(self, model, beats_per_bar=None, min_bpm=55., max_bpm=215.,
               num_tempi=60, transition_lambda=100,
               observation_lambda=16, threshold=0.05,
               correct=True, fps=50):
    """
    Parameters
    ----------
    model : int
        1, 2, or 3 – selects which pre-trained CRNN weights to load
        (exactly as before).
    debug : bool, optional
        If True, every madmom processor in the front-end prints a one-line
        summary of the tensor it returns (shape, min, max, mean, std).
        Production default is False (silent behaviour).
    """
    if beats_per_bar is None:
      beats_per_bar = [2, 3, 4]

    self.sample_rate = 22050
    self.log_spec_sample_rate = self.sample_rate
    self.log_spec_hop_length = int(20 * 0.001 * self.log_spec_sample_rate)
    self.log_spec_win_length = int(64 * 0.001 * self.log_spec_sample_rate)

    # expand arguments to arrays
    beats_per_bar = np.array(beats_per_bar, ndmin=1)
    min_bpm = np.array(min_bpm, ndmin=1)
    max_bpm = np.array(max_bpm, ndmin=1)
    num_tempi = np.array(num_tempi, ndmin=1)
    transition_lambda = np.array(transition_lambda, ndmin=1)

    # make sure the other arguments are long enough by repeating them
    if len(min_bpm) != len(beats_per_bar):
      min_bpm = np.repeat(min_bpm, len(beats_per_bar))
    if len(max_bpm) != len(beats_per_bar):
      max_bpm = np.repeat(max_bpm, len(beats_per_bar))
    if len(num_tempi) != len(beats_per_bar):
      num_tempi = np.repeat(num_tempi, len(beats_per_bar))
    if len(transition_lambda) != len(beats_per_bar):
      transition_lambda = np.repeat(transition_lambda,
                                    len(beats_per_bar))
    if not (len(min_bpm) == len(max_bpm) == len(num_tempi) ==
            len(beats_per_bar) == len(transition_lambda)):
      raise ValueError('`min_bpm`, `max_bpm`, `num_tempi`, `num_beats` '
                       'and `transition_lambda` must all have the same '
                       'length.')
    self.map = map

    # convert timing information to construct a beat state space
    min_interval = 60. * fps / max_bpm
    max_interval = 60. * fps / min_bpm

    # model the different bar lengths
    self.hmms = []

    for b, beats in enumerate(beats_per_bar):
      st = BarStateSpace(beats, min_interval[b], max_interval[b],
                         num_tempi[b])
      tm = BarTransitionModel(st, transition_lambda[b])
      om = RNNDownBeatTrackingObservationModel(st, observation_lambda)
      self.hmms.append(HiddenMarkovModel(tm, om))

    self.beats_per_bar = beats_per_bar
    self.threshold = threshold
    self.correct = correct
    self.fps = fps
    self.model = BDA(272, 150, 2)  # Beat–Downbeat Activation detector

    script_dir = os.path.dirname(__file__)
    if model == 1:
      weights = 'models/model_1_weights.pt'
    elif model == 2:
      weights = 'models/model_2_weights.pt'
    elif model == 3:
      weights = 'models/model_3_weights.pt'
    else:
      raise RuntimeError(f'Failed to open the trained model: {model}')

    self.model.load_state_dict(
      torch.load(os.path.join(script_dir, weights)),
      strict=False
    )
    self.model.eval()

  def process(self, audio_path=None):
    if isinstance(audio_path, str) or audio_path.all() is not None:
      start = time.perf_counter()

      with torch.no_grad():
        if isinstance(audio_path, str):
          audio, _ = librosa.load(audio_path, sr=self.sample_rate)  # reading the data
        elif len(np.shape(audio_path)) > 1:
          audio = np.mean(audio_path, axis=1)
        else:
          audio = audio_path
        feats1 = compute_features(audio)
        print("process_audio->feats", feats1.shape, feats1.min(), feats1.max(), feats1.mean(), feats1.std())
        feats = feats1.T.T
        feats = torch.from_numpy(feats)
        feats = feats.unsqueeze(0).to('cpu')
        preds = self.model(feats)[0]  # extracting the activations by passing the feature through the NN
        preds = self.model.final_pred(preds)
        preds = preds.cpu().detach().numpy()
        preds = np.transpose(preds[:2, :])

      elapsed = time.perf_counter() - start
      print(f"[{elapsed:.6f} s] BeatNet pass done")

      activations = preds
      # use only the activations > threshold (init offset to be added later)
      first = 0
      if self.threshold:
        activations, first = threshold_activations(activations,
                                                   self.threshold)
      # return no beats if no activations given / remain after thresholding
      if not activations.any():
        return np.empty((0, 2))
      # (parallel) decoding of the activations with HMM
      results = list(self.map(_process_dbn, zip(self.hmms,
                                                it.repeat(activations))))
      # choose the best HMM (highest log probability)
      best = np.argmax(list(r[1] for r in results))
      # the best path through the state space
      path, _ = results[best]
      # the state space and observation model of the best HMM
      st = self.hmms[best].transition_model.state_space
      om = self.hmms[best].observation_model
      # the positions inside the pattern (0..num_beats)
      positions = st.state_positions[path]
      # corresponding beats (add 1 for natural counting)
      beat_numbers = positions.astype(int) + 1
      if self.correct:
        beats = np.empty(0, dtype=int)
        # for each detection determine the "beat range", i.e. states where
        # the pointers of the observation model are >= 1
        beat_range = om.pointers[path] >= 1
        # if there aren't any in the beat range, there are no beats
        if not beat_range.any():
          return np.empty((0, 2))
        # get all change points between True and False (cast to int before)
        idx = np.nonzero(np.diff(beat_range.astype(int)))[0] + 1
        # if the first frame is in the beat range, add a change at frame 0
        if beat_range[0]:
          idx = np.r_[0, idx]
        # if the last frame is in the beat range, append the length of the
        # array
        if beat_range[-1]:
          idx = np.r_[idx, beat_range.size]
        # iterate over all regions
        if idx.any():
          for left, right in idx.reshape((-1, 2)):
            # pick the frame with the highest activations value
            # Note: we look for both beats and down-beat activations;
            #       since np.argmax works on the flattened array, we
            #       need to divide by 2
            peak = np.argmax(activations[left:right]) // 2 + left
            beats = np.hstack((beats, peak))
      else:
        # transitions are the points where the beat numbers change
        # FIXME: we might miss the first or last beat!
        #        we could calculate the interval towards the beginning/end
        #        to decide whether to include these points
        beats = np.nonzero(np.diff(beat_numbers))[0] + 1

      output = np.vstack(((beats + first) / float(self.fps), beat_numbers[beats])).T

      # output = self.estimator.process(preds)  # Using DBN offline inference to infer beat/downbeats
      elapsed = time.perf_counter() - start
      print(f"[{elapsed:.6f} s] HMM-DBN pass done")

      times = output[:, 0]  # float seconds – keep verbatim
      beats = output[:, 1].astype(int)  # cast to int
      mapping = dict(zip(times.tolist(), beats.tolist()))

      return json.dumps(mapping, indent=2)

    else:
      raise RuntimeError('An audio object or file directory is required for the offline usage!')
