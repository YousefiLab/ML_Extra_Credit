from __future__ import print_function

import os

import kapre
from keras.models import load_model

os.environ["THEANO_FLAGS"] = "device=gpu0"
import sys
sys.path.append('./gumpy')

import gumpy
import numpy as np
from sklearn.metrics import confusion_matrix, cohen_kappa_score
from keras.models import load_model


import utils

utils.print_version_info()

# ## Setup parameters for the model and data
# Before we jump into the processing, we first wish to specify some parameters (e.g. frequencies) that we know from the data.

DEBUG = True
CLASS_COUNT = 2
DROPOUT = 0.2  # dropout rate in float

# parameters for filtering data
FS = 250
LOWCUT = 2
HIGHCUT = 60
ANTI_DRIFT = 0.5
CUTOFF = 50.0  # freq to be removed from signal (Hz) for notch filter
Q = 30.0  # quality factor for notch filter
W0 = CUTOFF / (FS / 2)
AXIS = 0

# set random seed
SEED = 42
KFOLD = 5
NumbItr = 10

# ## Load raw data
#
# Before training and testing a model, we need some data. The following code shows how to load a dataset using ``gumpy``.

# specify the location of the GrazB datasets
data_dir = './data/Graz'
subject = 'B01'

# initialize the data-structure, but do _not_ load the data yet
grazb_data = gumpy.data.GrazB(data_dir, subject)

# now that the dataset is setup, we can load the data. This will be handled from within the utils function,
# which will first load the data and subsequently filter it using a notch and a bandpass filter.
# the utility function will then return the training data.
x_train, y_train = utils.load_preprocess_data(grazb_data, True, LOWCUT, HIGHCUT, W0, Q, ANTI_DRIFT, CLASS_COUNT, CUTOFF,
                                              AXIS, FS)

# ## Augment data

x_augmented, y_augmented = gumpy.signal.sliding_window(data=x_train[:, :, :],
                                                       labels=y_train[:, :],
                                                       window_sz=4 * FS,
                                                       n_hop=FS // 10,
                                                       n_start=FS * 1)
x_subject = x_augmented
y_subject = y_augmented
x_subject = np.rollaxis(x_subject, 2, 1)

# # CNN model


# from .model import KerasModel
from keras.models import Sequential
from keras.layers import Dense, Activation, Flatten
from keras.layers import BatchNormalization, Dropout, Conv2D, MaxPooling2D
from kapre.utils import Normalization2D
from kapre.time_frequency import Spectrogram


def CNN_model(input_shape, dropout=0.5, print_summary=False):
    # basis of the CNN_STFT is a Sequential network
    model = Sequential()

    # spectrogram creation using STFT
    model.add(Spectrogram(n_dft=128, n_hop=16, input_shape=input_shape,
                          return_decibel_spectrogram=False, power_spectrogram=2.0,
                          trainable_kernel=False, name='static_stft'))
    model.add(Normalization2D(str_axis='freq'))

    # Conv Block 1
    model.add(Conv2D(filters=24, kernel_size=(12, 12),
                     strides=(1, 1), name='conv1',
                     border_mode='same'))
    model.add(BatchNormalization(axis=1))
    model.add(Activation('relu'))
    model.add(MaxPooling2D(pool_size=(2, 2), strides=(2, 2), padding='valid',
                           data_format='channels_last'))

    # Conv Block 2
    model.add(Conv2D(filters=48, kernel_size=(8, 8),
                     name='conv2', border_mode='same'))
    model.add(BatchNormalization(axis=1))
    model.add(Activation('relu'))
    model.add(MaxPooling2D(pool_size=(2, 2), strides=(2, 2), padding='valid',
                           data_format='channels_last'))

    # Conv Block 3
    model.add(Conv2D(filters=96, kernel_size=(4, 4),
                     name='conv3', border_mode='same'))
    model.add(BatchNormalization(axis=1))
    model.add(Activation('relu'))
    model.add(MaxPooling2D(pool_size=(2, 2), strides=(2, 2),
                           padding='valid',
                           data_format='channels_last'))
    model.add(Dropout(dropout))

    # classificator
    model.add(Flatten())
    model.add(Dense(2))  # two classes only
    model.add(Activation('softmax'))

    if print_summary:
        print(model.summary())

    # compile the model
    model.compile(loss='categorical_crossentropy',
                  optimizer='adam',
                  metrics=['accuracy'])

    # assign model and return

    return model


from sklearn.model_selection import StratifiedKFold
from keras.callbacks import ModelCheckpoint
# define KFOLD-fold cross validation test harness
kfold = StratifiedKFold(n_splits=KFOLD, shuffle=True, random_state=SEED)
cvscores = []
ii = 1
conf_matrix_testing = None
conf_matrix_training = None
training_kappas = []
testing_kappas = []
for train, test in kfold.split(x_subject, y_subject[:, 0]):
    print('Run ' + str(ii) + '...')
    # create callbacks
    model_name_str = "ModelSave/" + 'GRAZ_CNN_STFT_3layer_' + \
                     '_run_' + str(ii)

    checkpoint = ModelCheckpoint(model_name_str, monitor='val_acc', verbose=1, save_best_only=True, mode='max')
    callbacks_list = []
    # Fit the model
    # initialize and create the model
    model = CNN_model(x_subject.shape[1:], dropout=DROPOUT, print_summary=False)

    # fit model. If you specify monitor=True, then the model will create callbacks
    # and write its state to a HDF5 file
    model.fit(x_subject[train], y_subject[train],
              epochs=NumbItr,
              batch_size=256,
              verbose=1,
              validation_split=0.1, callbacks=callbacks_list)

    # evaluate the model
    print('Evaluating model on test set...')
    scores = model.evaluate(x_subject[test], y_subject[test], verbose=0)
    print("Result on test set: %s: %.2f%%" % (model.metrics_names[1], scores[1] * 100))
    cvscores.append(scores[1] * 100)

    # useful for metrics
    true_labels_train = np.array(y_subject[train]).argmax(axis=-1)
    pred_labels_train = model.predict(x_subject[train]).argmax(axis=-1)
    true_labels_test = np.array(y_subject[test]).argmax(axis=-1)
    pred_labels_test = model.predict(x_subject[test]).argmax(axis=-1)

    # calc confusion matrices
    conf_matrix_testing = confusion_matrix(true_labels_test, pred_labels_test)
    conf_matrix_training = confusion_matrix(true_labels_train, pred_labels_train)

    # calc kappa coefficients
    testing_kappa = cohen_kappa_score(true_labels_test, pred_labels_test)
    training_kappa = cohen_kappa_score(true_labels_train, pred_labels_train)
    print("Training kappa: {:.3f}  Testing kappa: {:.3f}".format(training_kappa, testing_kappa))

    if ii == kfold:
        model.save('CNN_with_spectrogram_.h5')  # save the model for future use
        print("Model saved to disk!")

    ii += 1

# print some evaluation statistics and write results to file
print("%.2f%% (+/- %.2f%%)" % (np.mean(cvscores), np.std(cvscores)))
cv_all_subjects = np.asarray(cvscores)
# print('Saving CV values to file....')
# np.savetxt("Results/" + 'GRAZ_CV_' + 'CNN_STFT_3layer_' + str(DROPOUT) + 'do' + '.csv',
#            cv_all_subjects, delimiter=',', fmt='%2.4f')
# print('CV values successfully saved!\n')

print("Confusion matrix of last fold (training):  with kappa: {}".format(training_kappas[-1]))
print(conf_matrix_training)
print("Confusion matrix of last fold (testing):  with kappa: {}".format(testing_kappas[-1]))
print(conf_matrix_testing)

# model2 = load_model('CNN_with_spectrogram_.h5',
#                     custom_objects={'Spectrogram': kapre.time_frequency.Spectrogram,
#                                     'Normalization2D': kapre.utils.Normalization2D})
