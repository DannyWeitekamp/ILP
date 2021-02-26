from numbaILP.structref import define_structref
from numbaILP.utils import _struct_from_pointer, _pointer_from_struct, _pointer_from_struct_incref
from numba.experimental.structref import new
import numpy as np
import numba
from numba import types, njit, guvectorize,vectorize,prange, jit, literally
# from numba.experimental import jitclass
from numba import deferred_type, optional
from numba import void,b1,u1,u2,u4,u8,i1,i2,i4,i8,f4,f8,c8,c16
from numba.typed import List, Dict
from numba.core.types import DictType,ListType, unicode_type, NamedTuple,NamedUniTuple,Tuple
from collections import namedtuple
import timeit
from sklearn import tree as SKTree
import os 

from numba import config, njit, threading_layer
from numba.np.ufunc.parallel import _get_thread_id

config.THREADING_LAYER = 'thread_safe'
print("n threads", config.NUMBA_NUM_THREADS)
# os.environ['NUMBA_PARALLEL_DIAGNOSTICS'] = '1'

N = 100
def time_ms(f):
    f() #warm start
    return " %0.6f ms" % (1000.0*(timeit.timeit(f, number=N)/float(N)))

@njit(f8(u4,u4[:]), cache=True)
def gini(total,counts):
    if(total > 0):
        s = 0.0
        for c_i in counts:
            prob = c_i / total;
            s += prob * prob 
        return 1.0 - s
    else:
        return 0.0


@njit(nogil=True,fastmath=True,cache=False)
def unique_counts(inp):
    ''' 
        Finds the unique classes in an input array of class labels
    '''
    counts = [];
    uniques = [];
    inds = np.zeros(len(inp),dtype=np.uint32);
    ind=0;
    last = 0;
    for i in range(1,len(inp)):
        if(inp[i-1] != inp[i]):
            counts.append(i-last);
            uniques.append(inp[i-1]);
            last = i;
            ind += 1;
        inds[i] = ind;
    counts.append((i+1)-last);
    uniques.append(inp[i]);

    c = np.asarray(counts,dtype=np.uint32)
    u = np.asarray(uniques,dtype=np.int32)
    return c, u, inds


# counts_cache_fields = [
#     ('v_count_left', i8),
#     ('X', i4[:,:]),
#     ('Y', i4[:]),
# ]



nominal_split_cache_fields = [
    ('best_v', i4),
    ('v_counts', u4[:]),
    ('y_counts_per_v', u4[:,:]),
]

NominalSplitCache, NominalSplitCacheType = define_structref("NominalSplitCache",nominal_split_cache_fields, define_constructor=False) 

@njit(cache=True)
def init_nominal_split_cache(n_vals, n_classes):
    st = new(NominalSplitCacheType)
    # instantiate as one so it is gaurenteed to be contiguous
    # data = np.zeros((n_vals*(n_classes+1)))
    # st.v_counts = data[:n_vals]
    # st.y_counts_per_v = data[n_vals:n_vals*(n_classes+1)].reshape((n_vals,n_classes))
    st.best_v = -1
    st.v_counts = np.zeros((n_vals,),dtype=np.uint32)
    st.y_counts_per_v = np.zeros((n_vals,n_classes),dtype=np.uint32)
    return st

@njit(cache=True)
def expand_nominal_split_cache(st, n_vals,n_classes):
    v_counts = np.empty((n_vals,),dtype=np.uint32)
    v_counts[:len(st.v_counts)] = st.v_counts
    v_counts[len(st.v_counts):] = 0
    st.v_counts = v_counts

    y_counts_per_v = np.empty((n_vals,n_classes),dtype=np.uint32)
    shp = st.y_counts_per_v.shape
    y_counts_per_v[:shp[0],:shp[1]] = st.y_counts_per_v
    y_counts_per_v[:shp[0],shp[1]:] = 0
    y_counts_per_v[:shp[0]] = 0
    st.y_counts_per_v = y_counts_per_v
    return st






continous_split_cache_field = [
    ('is_const', u1),
    ('threshold', f8),
    ('op', i4),
    ('left_counts', i4[:]),
    ('right_counts', i4[:]),
    ('nan_counts', i4[:]),
]

ContinousSplitCache, ContinousSplitCacheType = define_structref("ContinousSplitCache",continous_split_cache_field, define_constructor=False) 

# counts_imps = np.dtype([('left_count', np.float64), ('col', np.float64)])

splitter_context_fields = [
    #The time of the most recent update to this context
    ('t_last_update', i4),

    #A pointer to the parent split context
    ('parent_ptr', i8),

    #The feature matrix
    ('X', i4[:,:]),
    #The label list
    ('Y', i4[:]),

    # The number of unique values per nominal feature 
    ('n_vals', i4[::1]),
    
    # Idea borrowed from sklearn, sample_inds are preallocated and 
    #  kept contiguous in each node by swapping left and right indicies 
    #  then only 'start' and 'end' need to be passed instead of copying the indicies
    ('sample_inds', i8[::1]),
    ('start',i8),
    ('end', i8),


    ('feature_inds', i8[::1]),
    # The total number of samples for this node
    ('n_samples', i4),
    # The number of constant features
    ('n_const_fts', i8),
    # The total number of features
    ('n_features', i4),

    # The counts of each class label in this node
    ('y_counts',u4[:]),
    # The number of unique class labels 
    ('n_classes',i4),
    
    #The impurity of the node before splitting
    ('impurity', f8),
    #The total, left, and right impurities of all the splits f8[n_features,3]
    ('impurities', f8[:,:]),
    #The impurity of the node after the best split
    ('best_split_impurity', f8),

    #Whether the best split is nominal 0 or continuous 1
    ('best_is_continous', u1),
    ('best_split', i4),
    

    # In the nominal case the value of the best literal
    ('best_val', i4),
    # In the continous case the value of the best threshold
    ('best_thresh', f4),


    # Whether or not the y_counts associated with selecting on each nominal
    #  value are cached
    ('nominal_split_cache_ptrs', i8[:]),
    ('continous_split_cache_ptrs', i8[:]),
    # ('val_y_counts_cached', u1),
    # # Whether or not the left and right y_counts are cached 
    # ('split_y_counts_cached', u1),
    
    # A raw data array that holds for each feature:
    #  v_count: u4[n_vals_j]
    #  y_counts : u4[n_vals_j, n_classes]
    # ('val_vy_count_caches', u4[:]),
    # ???
    # ('split_y_count_caches', u4[:]),
    # A cache of the best thresholds for each continous split
    # ('threshold_cache', f4[:]),

]


SplitterContext, SplitterContextType = define_structref("SplitterContext",splitter_context_fields, define_constructor=False) 

@njit(cache=True)
def new_splitter_context(parent_ptr, start, end, y_counts, impurity):
    st = new(SplitterContextType)
    # st.counts_cached = False
    st.parent_ptr = parent_ptr
    # st.sample_inds = sample_inds
    st.start = start
    st.end = end
    st.n_samples = end-start

    # st.n_classes = n_classes
    st.y_counts = y_counts

    st.impurity = impurity
    st.best_split_impurity = np.inf

    st.nominal_split_cache_ptrs = np.zeros((32,),dtype=np.int64)
    st.continous_split_cache_ptrs = np.zeros((32,),dtype=np.int64)

    # st.counts_imps = np.zeros(n_features, ((n_classes)*2)+6,dtype=np.int32)
    if(parent_ptr != 0):
        parent = _struct_from_pointer(SplitterContextType, parent_ptr)
        st.n_vals = parent.n_vals
        st.sample_inds = parent.sample_inds
        st.n_classes = parent.n_classes
        st.n_features = parent.n_features
        st.feature_inds = parent.feature_inds
        st.X = parent.X
        st.Y = parent.Y
        # st.n_vals = parent.n_vals
    return st


@njit(cache=True,parallel=True)
def update_nominal_impurities(splitter_context):
    #Make various variables local
    sc = splitter_context
    n_samples, start, end = sc.n_samples, sc.start, sc.end
    n_classes = sc.n_classes
    # counts_imps = sc.counts_imps
    sample_inds = sc.sample_inds
    feature_inds = sc.feature_inds
    n_const_fts = sc.n_const_fts
    n_vals = sc.n_vals
    y_counts = sc.y_counts
    impurity = sc.impurity
    X, Y = sc.X, sc.Y

    # Grow the count cache if the number of features has increased
    len_cache = len(sc.nominal_split_cache_ptrs)
    if(len_cache < X.shape[1]):
        new_sp_ptrs = np.zeros((X.shape[1],),dtype=np.int64)
        new_sp_ptrs[:len_cache] = sc.nominal_split_cache_ptrs
        sc.nominal_split_cache_ptrs = new_sp_ptrs

    # X_inds = X[inds]

    # y_count_left = counts_imps[:]

    # n_non_const = len(feature_inds)#-n_const_fts
    # print(len(feature_inds), n_const_fts, n_non_const)

    impurities = np.empty((X.shape[1],3),dtype=np.float64)
    # b_split, b_split_imp_total = 0, np.inf
    #Go through the samples in Fortran order (i.e. feature then sample)
    # for k_j in prange(0,n_non_const):
    for k_j in prange(X.shape[1]):
        # print(_get_thread_id(),k_j)
        j = k_j#feature_inds[k_j]
        n_vals_j = n_vals[j]
        cache_ptr = sc.nominal_split_cache_ptrs[j]

        if(cache_ptr != 0):
            split_cache = _struct_from_pointer(NominalSplitCacheType, cache_ptr)
            split_cache_shape = split_cache.y_counts_per_v.shape
            if(split_cache_shape[0] != n_vals_j or split_cache_shape[1] != n_classes):
                expand_nominal_split_cache(split_cache, n_vals_j, n_classes)
        else:
            split_cache = init_nominal_split_cache(n_vals_j, n_classes)
            sc.nominal_split_cache_ptrs[j] = _pointer_from_struct_incref(split_cache)

        # print(cache_ptr,sc.nominal_split_cache_ptrs[j])


        v_counts       = split_cache.v_counts
        y_counts_per_v = split_cache.y_counts_per_v

        # else:
        # y_counts_per_feature = np.zeros((n_vals_j,n_classes),dtype=np.uint32)
        # v_counts_per_feature = np.zeros((n_vals_j),dtype=np.uint32)


        #Update the feature counts for labels and values
        for k_i in range(start, end):
            i = sample_inds[k_i]
            y_i = Y[i]
            y_counts_per_v[X[i,j],y_i] += 1
            v_counts[X[i,j]] += 1
            # for c in range(n_vals_j):
        

        #If this feature is found to be constant then skip computing impurity
        if(np.sum(v_counts > 0) <= 1):
            impurities[k_j,0] = impurity
            impurities[k_j,1] = impurity
            impurities[k_j,2] = impurity
        else:
            b_imp_tot, b_imp_l, b_imp_r, b_ft_val = np.inf, 0, 0, 0
            for ft_val in range(n_vals_j):
                counts_r = y_counts_per_v[ft_val]
                total_r = np.sum(counts_r)

                counts_l = y_counts-counts_r
                total_l = n_samples-total_r

                imp_l = gini(total_l, counts_l)
                imp_r = gini(total_r, counts_r)

                imp_tot = ((total_l/n_samples) * imp_l) + ((total_r/n_samples) * imp_r)
                if(imp_tot < b_imp_tot):
                    b_imp_tot, b_imp_l, b_imp_r, b_ft_val = imp_tot, imp_l, imp_r, ft_val
            split_cache.best_v = b_ft_val
            impurities[k_j,0] = b_imp_tot
            impurities[k_j,1] = b_imp_l
            impurities[k_j,2] = b_imp_r

    sc.impurities = impurities
    # print(impurities)
    
    # print(best_ind)


@njit(cache=True)
def build_root_context(X,Y):
    sorted_inds = np.argsort(Y)
    # X = np.asfortranarray(X[sorted_inds])
    X = X[sorted_inds]
    Y = Y[sorted_inds]

    y_counts,u, inds = unique_counts(Y)
    n_classes = len(u)
    
    sample_inds = np.arange(len(Y))

    impurity = gini(len(Y),y_counts)

    c = new_splitter_context(0, 0 ,len(Y) ,y_counts, impurity)
    c.X = X
    c.Y = Y
    c.n_vals = (np.ones((X.shape[1],))*5).astype(np.int32)
    c.sample_inds = sample_inds
    c.n_classes = n_classes
    c.n_features = X.shape[1]
    c.feature_inds = np.arange(X.shape[1])
    return c



@njit(cache=True)
def fit_tree(X, Y,iterative=False):
    c = build_root_context(X,Y)
    stack = List.empty_list(SplitterContextType)
    stack.append(c)
    while(len(stack) > 0):
        c = stack.pop()
        update_nominal_impurities(c)
        # print(c.impurities[:,0],c.start,c.end)
        best_split = np.argmin(c.impurities[:,0])
        bst_imps = c.impurities[best_split]
        imp_tot, imp_l, imp_r = bst_imps[0], bst_imps[1], bst_imps[2]

        splt_c = _struct_from_pointer(NominalSplitCacheType, c.nominal_split_cache_ptrs[best_split])
        y_counts_r = splt_c.y_counts_per_v[splt_c.best_v]
        y_counts_l = c.y_counts - y_counts_r
        
        

        p, p_end, sample_inds = c.start, c.end, c.sample_inds

        # Sklearn inplace reordering trick
        # print(sample_inds)
        while p < p_end:
            if c.X[sample_inds[p], best_split]==splt_c.best_v:
                p += 1
            else:
                p_end -= 1
                sample_inds[p], sample_inds[p_end] = sample_inds[p_end], sample_inds[p]
        c.sample_inds = sample_inds
        # print(sample_inds[c.start:c.end])
        # print(c.X[sample_inds[c.start:p_end]][:,best_split], c.X[sample_inds[p_end:c.end]][:,best_split])
        # print(best_split, "==", splt_c.best_v)
        
        

        # print(y_counts_l, y_counts_r)

        ptr = _pointer_from_struct_incref(c)

        

        if(c.impurity - imp_tot > 0):
            # print('p_end', p_end, imp_l, imp_r)
            if(imp_l > 0):
                c_l = new_splitter_context(ptr, p_end, c.end , y_counts_r, imp_l)
                stack.append(c_l)

            if(imp_r > 0):
                c_r = new_splitter_context(ptr, c.start    , p_end, y_counts_l, imp_r)
                stack.append(c_r)

        # print(len(stack))





    
@njit(cache=True)
def build_XY(N=1000,M=100):
    p0 = np.array([1,1,1,0,0],dtype=np.int32)
    p1 = np.array([0,1,1,1,0],dtype=np.int32)
    p2 = np.array([0,0,1,1,1],dtype=np.int32)
    p3 = np.array([1,0,1,1,0],dtype=np.int32)
    p4 = np.array([1,0,1,0,1],dtype=np.int32)
    X = np.random.randint(0,5,(N,M)).astype(np.int32)
    Y = np.random.randint(0,3,(N,)).astype(np.int32)

    for x, y in zip(X,Y):
        if(y == 0): 
            x[:5] = np.where(p0,p0,x[:5])
        elif(y==1):
            x[:5] = np.where(p1,p1,x[:5])
        elif(y==2):
            x[:5] = np.where(p2,p2,x[:5])
        elif(y==3):
            x[:5] = np.where(p3,p3,x[:5])
        elif(y==4):
            x[:5] = np.where(p4,p4,x[:5])
    return X, Y


# X, Y = build_XY(10,10)
X, Y = build_XY()
# @njit(cache=True)
def test_fit_tree():
    fit_tree(X, Y)
    

def test_sklearn():
    clf = SKTree.DecisionTreeClassifier()
    clf.fit(X,Y)


# test_fit_tree()
print(time_ms(test_fit_tree))
print(time_ms(test_sklearn))























                


            # counts_imps[0+c+y_j] += 1












